"""K线和技术指标采集器 - 基于腾讯 API（更稳定）"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import threading
import time

from src.platform.marketdata.collectors.market_http import fetch_source
from src.platform.marketdata.models import MARKETS, MarketCode

logger = logging.getLogger(__name__)


def get_market_data():
    """惰性 import,避免包未装/循环 import 影响本模块加载。"""
    from src.platform.marketdata.marketdata_client import get_market_data as _g
    return _g()


# Dấu nguồn gọi gom hết về market_http (cả dự án dùng chung một contextvar).
# Giữ lại tên kline_source để tương thích các phía gọi sẵn có (bộ lập lịch...).
kline_source = fetch_source


# ── Bộ đệm nến theo trạng thái thị trường ─────────────────────────────────
# Nến ngày mỗi ngày chỉ chốt một lần (sau khi đóng cửa), nhưng tác vụ lập lịch vòng nào cũng gọi mạng lấy lại từng mã → dồn dập gây giới hạn tốc độ.
# Trong phiên dùng TTL ngắn (cây nến cuối còn động), sau khi đóng cửa dùng TTL dài (dữ liệu đã chốt, khỏi lấy lại).
_KLINE_CACHE: dict[str, tuple[float, int, list["KlineData"]]] = {}
_KLINE_TTL_TRADING_S = 180
_KLINE_TTL_CLOSED_S = 1800

# Negative cache khi lỗi: nguồn hỏng tạm thời (Server disconnected / giới hạn tốc độ) thì trong cửa sổ chờ không gọi mạng nữa.
# Các bên tiêu thụ hàng loạt vừa được khôi phục (entry_candidates / strategy_engine / backtest / phân rã đóng góp danh mục) sẽ song song
# lấy dữ liệu cùng một rổ mã; không đệm kết quả rỗng thì mỗi bên mỗi vòng lại dội lên nguồn dữ liệu.
_FAIL_UNTIL: dict[str, float] = {}
_FAIL_COOLDOWN_S = 60.0  # Trong phiên: thời gian chờ ngắn để thử lại sớm
_FAIL_COOLDOWN_CLOSED_S = 900.0  # Sau khi đóng cửa: dữ liệu đã chốt, nên lỗi / thiếu thì chờ lâu, tránh tác vụ hàng loạt gọi dồn dập


def _fail_cooldown(market: MarketCode) -> float:
    """取数失败/不足时的冷却时长:交易时段短(尽快重试),收盘后长(重试无意义且易刷屏)。"""
    try:
        md = MARKETS.get(market)
        if md and md.is_trading_time():
            return _FAIL_COOLDOWN_S
    except Exception:
        pass
    return _FAIL_COOLDOWN_CLOSED_S


# Gộp truy cập song song cùng mã: các lần lấy dữ liệu cùng cache_key được nối tiếp lại, chỉ gọi mạng một lần, phần còn lại dùng bộ đệm.
_FETCH_LOCKS: dict[str, threading.Lock] = {}
_FETCH_LOCKS_GUARD = threading.Lock()


def _get_fetch_lock(cache_key: str) -> threading.Lock:
    """返回某 cache_key 的取数锁(进程内复用),用于合并同标的并发请求。"""
    with _FETCH_LOCKS_GUARD:
        lk = _FETCH_LOCKS.get(cache_key)
        if lk is None:
            lk = threading.Lock()
            _FETCH_LOCKS[cache_key] = lk
        return lk


def _kline_cache_ttl(market: MarketCode) -> float:
    try:
        md = MARKETS.get(market)
        if md and md.is_trading_time():
            return _KLINE_TTL_TRADING_S
    except Exception:
        pass
    return _KLINE_TTL_CLOSED_S


def clear_kline_cache() -> None:
    """清空 K线内存缓存与失败冷却标记(测试隔离用)。"""
    _KLINE_CACHE.clear()
    _FAIL_UNTIL.clear()


def get_index_klines(index_code: str, market: MarketCode, days: int = 120) -> list[KlineData]:
    """取大盘/指数日K:走 marketdata 包 index_klines(INDEX_SECID 显式映射,未映射如美股指数
    → 空列表,fail-soft;见 packages/marketdata/src/marketdata/client.py)。
    """
    try:
        bars = get_market_data().index_klines(index_code, market=market.value, days=days)
    except Exception as e:
        logger.debug(f"指数K线获取失败 {index_code}: {e}")
        return []
    return [
        KlineData(date=b.date, open=b.open, close=b.close, high=b.high, low=b.low, volume=b.volume)
        for b in bars
    ]


@dataclass
class KlineData:
    """K线数据"""

    date: str
    open: float
    close: float
    high: float
    low: float
    volume: float


@dataclass
class TechnicalIndicators:
    """技术指标"""

    # Đường trung bình
    ma5: float | None = None
    ma10: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    # MACD
    macd_dif: float | None = None
    macd_dea: float | None = None
    macd_hist: float | None = None
    macd_cross: str | None = None  # Giao cắt vàng / giao cắt tử
    macd_cross_days: int | None = None  # Số phiên kể từ lần giao cắt gần nhất
    # RSI
    rsi6: float | None = None
    rsi12: float | None = None
    rsi24: float | None = None
    # KDJ
    kdj_k: float | None = None
    kdj_d: float | None = None
    kdj_j: float | None = None
    kdj_cross: str | None = None  # Giao cắt vàng / giao cắt tử
    # Dải Bollinger
    boll_upper: float | None = None
    boll_mid: float | None = None
    boll_lower: float | None = None
    boll_width: float | None = None  # Phần trăm độ rộng dải
    # Sức khối lượng
    volume_ratio: float | None = None  # Tỷ lệ khối lượng (khối lượng hôm nay / khối lượng bình quân 5 phiên)
    volume_ma5: float | None = None
    volume_ma10: float | None = None
    volume_trend: str | None = None  # Bùng khối lượng / cạn khối lượng / khối lượng đi ngang
    # Biên độ
    change_5d: float | None = None
    change_20d: float | None = None
    # Biên dao động
    amplitude: float | None = None  # Biên dao động hôm nay
    amplitude_avg5: float | None = None  # Biên dao động bình quân 5 phiên
    # Độ biến động (ATR)
    atr: float | None = None  # Biên dao động thực trung bình (giá trị tuyệt đối)
    atr_pct: float | None = None  # ATR / giá đóng cửa mới nhất * 100 (độ biến động tương đối, %)
    # Hỗ trợ và kháng cự (nhiều khung)
    support_s: float | None = None  # Hỗ trợ ngắn hạn (5 phiên)
    support_m: float | None = None  # Hỗ trợ trung hạn (20 phiên)
    support_l: float | None = None  # Hỗ trợ dài hạn (60 phiên)
    resistance_s: float | None = None  # Kháng cự ngắn hạn
    resistance_m: float | None = None  # Kháng cự trung hạn
    resistance_l: float | None = None  # Kháng cự dài hạn
    # Tương thích trường cũ
    support: float | None = None
    resistance: float | None = None
    # Mẫu hình nến
    kline_pattern: str | None = None  # Nến doji / nến búa / mẫu hình nhấn chìm...


def _calculate_ma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _ema(data: list[float], period: int) -> list[float]:
    """计算 EMA"""
    if not data:
        return []
    result = [data[0]]
    multiplier = 2 / (period + 1)
    for price in data[1:]:
        result.append((price - result[-1]) * multiplier + result[-1])
    return result


def _calculate_atr(klines: list[KlineData], period: int = 14) -> float | None:
    """计算 ATR(平均真实波幅)。

    TR = max(high-low, |high-prevClose|, |low-prevClose|)。
    与本模块其它指标一致,取最近 period 个 TR 的简单均值(非 Wilder 递归平滑),
    便于复现与手算校验。

    需要至少 period+1 根 K 线(才能算出 period 个含前收的 TR);
    数据不足或异常一律返回 None,不抛异常(fail-soft)。
    """
    try:
        if not klines or len(klines) < period + 1:
            return None
        trs: list[float] = []
        for i in range(1, len(klines)):
            cur = klines[i]
            prev_close = klines[i - 1].close
            tr = max(
                cur.high - cur.low,
                abs(cur.high - prev_close),
                abs(cur.low - prev_close),
            )
            trs.append(tr)
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period
    except Exception:
        return None


def _calculate_macd(
    closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float], list[float], list[float]] | None:
    """计算 MACD，返回完整序列用于判断交叉"""
    if len(closes) < slow + signal:
        return None

    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    dif = [f - s for f, s in zip(ema_fast, ema_slow)]
    dea = _ema(dif, signal)
    macd_hist = [(d - e) * 2 for d, e in zip(dif, dea)]
    return dif, dea, macd_hist


def _calculate_rsi(closes: list[float], period: int) -> float | None:
    """计算 RSI"""
    if len(closes) < period + 1:
        return None

    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    # Tính trên period phiên gần nhất
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _calculate_kdj(
    klines: list[KlineData], n: int = 9, m1: int = 3, m2: int = 3
) -> tuple[list[float], list[float], list[float]] | None:
    """计算 KDJ，返回完整序列"""
    if len(klines) < n:
        return None

    k_values = []
    d_values = []
    j_values = []

    for i in range(n - 1, len(klines)):
        period_klines = klines[i - n + 1 : i + 1]
        highest = max(k.high for k in period_klines)
        lowest = min(k.low for k in period_klines)
        close = klines[i].close

        if highest == lowest:
            rsv = 50
        else:
            rsv = (close - lowest) / (highest - lowest) * 100

        if not k_values:
            k = 50
            d = 50
        else:
            k = (2 / 3) * k_values[-1] + (1 / 3) * rsv
            d = (2 / 3) * d_values[-1] + (1 / 3) * k

        j = 3 * k - 2 * d

        k_values.append(k)
        d_values.append(d)
        j_values.append(j)

    return k_values, d_values, j_values


def _calculate_boll(
    closes: list[float], period: int = 20, num_std: int = 2
) -> tuple[float, float, float, float] | None:
    """计算布林带：上轨、中轨、下轨、带宽"""
    if len(closes) < period:
        return None

    recent = closes[-period:]
    mid = sum(recent) / period
    variance = sum((x - mid) ** 2 for x in recent) / period
    std = variance**0.5

    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower) / mid * 100 if mid > 0 else 0

    return upper, mid, lower, width


def _detect_kline_pattern(klines: list[KlineData]) -> str | None:
    """检测 K 线形态"""
    if len(klines) < 2:
        return None

    curr = klines[-1]
    prev = klines[-2]

    body = abs(curr.close - curr.open)
    upper_shadow = curr.high - max(curr.close, curr.open)
    lower_shadow = min(curr.close, curr.open) - curr.low
    total_range = curr.high - curr.low

    if total_range == 0:
        return None

    body_ratio = body / total_range

    # Nến doji: thân nến rất nhỏ
    if body_ratio < 0.1:
        if upper_shadow > body * 2 and lower_shadow > body * 2:
            return "十字星"
        elif upper_shadow > body * 3:
            return "倒T字"
        elif lower_shadow > body * 3:
            return "T字线"

    # Nến búa: bóng dưới rất dài, thân nằm phía trên
    if lower_shadow > body * 2 and upper_shadow < body * 0.5:
        if curr.close > curr.open:
            return "锤子线(阳)"
        else:
            return "锤子线(阴)"

    # Búa ngược: bóng trên rất dài
    if upper_shadow > body * 2 and lower_shadow < body * 0.5:
        if curr.close > curr.open:
            return "倒锤子(阳)"
        else:
            return "射击之星"

    # Mẫu hình nhấn chìm
    prev_body = abs(prev.close - prev.open)
    if body > prev_body * 1.5:
        if prev.close < prev.open and curr.close > curr.open:  # Nến giảm trước, nến tăng sau
            if curr.close > prev.open and curr.open < prev.close:
                return "看涨吞没"
        elif prev.close > prev.open and curr.close < curr.open:  # Nến tăng trước, nến giảm sau
            if curr.open > prev.close and curr.close < prev.open:
                return "看跌吞没"

    # Nến tăng thân dài / nến giảm thân dài
    if body_ratio > 0.7:
        change_pct = (curr.close - curr.open) / curr.open * 100 if curr.open > 0 else 0
        if change_pct > 3:
            return "大阳线"
        elif change_pct < -3:
            return "大阴线"

    return None


def _find_cross_days(
    series1: list[float], series2: list[float], cross_type: str
) -> int | None:
    """找到最近一次交叉距今的天数"""
    if len(series1) < 2 or len(series2) < 2:
        return None

    for i in range(len(series1) - 2, -1, -1):
        if cross_type == "金叉":
            # Giao cắt vàng: series1 cắt lên qua series2
            if series1[i] <= series2[i] and series1[i + 1] > series2[i + 1]:
                return len(series1) - 1 - i
        else:
            # Giao cắt tử: series1 cắt xuống qua series2
            if series1[i] >= series2[i] and series1[i + 1] < series2[i + 1]:
                return len(series1) - 1 - i

    return None


class KlineCollector:
    """K线数据采集器（腾讯 API）"""

    def __init__(self, market: MarketCode):
        self.market = market

    def get_klines(self, symbol: str, days: int = 60) -> list[KlineData]:
        """获取日K线数据。

        正缓存(按市场状态 TTL)+ 同标的并发合并(只联网一次)+ 失败负缓存
        (源短暂故障时冷却窗口内不再联网),避免多消费者并发把数据源打爆。
        """
        cache_key = f"{self.market.value}:{symbol}"
        need = max(1, int(days or 1))

        # 1) Đường nhanh: khớp bộ đệm dương còn tươi, khỏi cần khóa
        hit = self._cache_hit(cache_key, need)
        if hit is not None:
            return hit

        # 2) Gộp truy cập song song cùng mã: chỉ một luồng thật sự gọi mạng, các luồng khác chờ rồi dùng lại kết quả
        with _get_fetch_lock(cache_key):
            hit = self._cache_hit(cache_key, need)
            if hit is not None:
                return hit

            now = time.time()
            # 3) Negative cache: mã vừa lỗi thì trong cửa sổ chờ trả dữ liệu cũ / rỗng, không gọi mạng nữa
            if now < _FAIL_UNTIL.get(cache_key, 0.0):
                stale = _KLINE_CACHE.get(cache_key)
                bars = stale[2] if stale else []
                return bars[-need:] if len(bars) > need else bars

            klines = self._fetch_all_sources(symbol, days)
            if klines and len(klines) >= need:
                # Thành công và đủ số bản ghi: chốt bộ đệm dương rồi xóa dấu thời gian chờ
                _KLINE_CACHE[cache_key] = (now, len(klines), list(klines))
                _FAIL_UNTIL.pop(cache_key, None)
            else:
                # Rỗng, hoặc lấy được một phần nhưng chưa đủ need (thường gặp: Tencent thiếu dữ liệu Hồng Kông + EastMoney bù thất bại,
                # bộ đệm dương vì count < need nên không bao giờ khớp → vòng nào cũng dội lên nguồn bù) → chốt thời gian chờ.
                # Kết quả một phần vẫn được đệm lại và phục vụ ngay trong cửa sổ chờ, tránh gọi mạng lặp đi lặp lại.
                if klines:
                    _KLINE_CACHE[cache_key] = (now, len(klines), list(klines))
                _FAIL_UNTIL[cache_key] = now + _fail_cooldown(self.market)
            return klines[-need:] if len(klines) > need else klines

    def _cache_hit(self, cache_key: str, need: int) -> list[KlineData] | None:
        """命中新鲜正缓存(TTL 内且条数足够)则返回切片,否则 None。"""
        cached = _KLINE_CACHE.get(cache_key)
        if (
            cached
            and (time.time() - cached[0]) < _kline_cache_ttl(self.market)
            and cached[1] >= need
        ):
            bars = cached[2]
            return bars[-need:] if len(bars) > need else bars
        return None

    def _fetch_all_sources(self, symbol: str, days: int) -> list[KlineData]:
        """走 marketdata 包取数(不含缓存/合并逻辑):Engine 按 DataSource 优先级 +
        min_count 取数(条数不足则换源/取最长,tencent → stooq(US) / eastmoney(CN/HK))。
        """
        need = (max(10, min(days, 30)) if self.market == MarketCode.US
                else (max(120, int(days * 0.6)) if self.market in (MarketCode.CN, MarketCode.HK) else 1))
        want = min(max(days, 3000), 20000) if self.market in (MarketCode.CN, MarketCode.HK) else days
        bars = get_market_data().klines(symbol, market=self.market.value, days=want, min_count=need)
        return [KlineData(date=b.date, open=b.open, close=b.close, high=b.high,
                          low=b.low, volume=b.volume) for b in bars]

    def get_technical_indicators(
        self, symbol: str = "", klines: list[KlineData] | None = None
    ) -> TechnicalIndicators:
        """计算技术指标(可传入已取的 klines 复用,避免重复联网)。"""
        if klines is None:
            klines = self.get_klines(symbol, days=120)

        if not klines:
            return TechnicalIndicators()

        closes = [k.close for k in klines]
        volumes = [k.volume for k in klines]

        # Đường trung bình
        ma5 = _calculate_ma(closes, 5)
        ma10 = _calculate_ma(closes, 10)
        ma20 = _calculate_ma(closes, 20)
        ma60 = _calculate_ma(closes, 60)

        # MACD
        macd_result = _calculate_macd(closes)
        macd_dif, macd_dea, macd_hist = None, None, None
        macd_cross, macd_cross_days = None, None
        if macd_result:
            dif_list, dea_list, hist_list = macd_result
            macd_dif = dif_list[-1]
            macd_dea = dea_list[-1]
            macd_hist = hist_list[-1]
            # Xác định giao cắt vàng / giao cắt tử
            if macd_dif > macd_dea:
                macd_cross = "金叉"
                macd_cross_days = _find_cross_days(dif_list, dea_list, "金叉")
            else:
                macd_cross = "死叉"
                macd_cross_days = _find_cross_days(dif_list, dea_list, "死叉")

        # RSI
        rsi6 = _calculate_rsi(closes, 6)
        rsi12 = _calculate_rsi(closes, 12)
        rsi24 = _calculate_rsi(closes, 24)

        # KDJ
        kdj_k, kdj_d, kdj_j = None, None, None
        kdj_cross = None
        kdj_result = _calculate_kdj(klines)
        if kdj_result:
            k_list, d_list, j_list = kdj_result
            kdj_k = k_list[-1]
            kdj_d = d_list[-1]
            kdj_j = j_list[-1]
            if kdj_k > kdj_d:
                kdj_cross = "金叉"
            else:
                kdj_cross = "死叉"

        # Dải Bollinger
        boll_upper, boll_mid, boll_lower, boll_width = None, None, None, None
        boll_result = _calculate_boll(closes)
        if boll_result:
            boll_upper, boll_mid, boll_lower, boll_width = boll_result

        # Phân tích sức khối lượng
        volume_ma5 = _calculate_ma(volumes, 5) if volumes else None
        volume_ma10 = _calculate_ma(volumes, 10) if volumes else None
        volume_ratio = None
        volume_trend = None
        if volumes and volume_ma5 and volume_ma5 > 0:
            volume_ratio = volumes[-1] / volume_ma5
            if volume_ratio > 1.5:
                volume_trend = "放量"
            elif volume_ratio < 0.7:
                volume_trend = "缩量"
            else:
                volume_trend = "平量"

        # Biên độ
        change_5d = None
        change_20d = None
        if len(closes) >= 6:
            change_5d = (closes[-1] - closes[-6]) / closes[-6] * 100
        if len(closes) >= 21:
            change_20d = (closes[-1] - closes[-21]) / closes[-21] * 100

        # Biên dao động
        amplitude = None
        amplitude_avg5 = None
        if klines:
            curr = klines[-1]
            if curr.low > 0:
                amplitude = (curr.high - curr.low) / curr.low * 100
            if len(klines) >= 5:
                amps = []
                for k in klines[-5:]:
                    if k.low > 0:
                        amps.append((k.high - k.low) / k.low * 100)
                if amps:
                    amplitude_avg5 = sum(amps) / len(amps)

        # ATR (độ biến động): mốc dao động của chính mã đó, dùng cho phép xác định biến động bất thường thích ứng
        atr = _calculate_atr(klines, period=14)
        atr_pct = None
        if atr is not None and closes and closes[-1]:
            atr_pct = round(atr / closes[-1] * 100, 2)

        # Các vùng hỗ trợ và kháng cự nhiều tầng
        support_s, support_m, support_l = None, None, None
        resistance_s, resistance_m, resistance_l = None, None, None
        if len(klines) >= 5:
            support_s = min(k.low for k in klines[-5:])
            resistance_s = max(k.high for k in klines[-5:])
        if len(klines) >= 20:
            support_m = min(k.low for k in klines[-20:])
            resistance_m = max(k.high for k in klines[-20:])
        if len(klines) >= 60:
            support_l = min(k.low for k in klines[-60:])
            resistance_l = max(k.high for k in klines[-60:])

        # Tương thích trường cũ
        support = support_m
        resistance = resistance_m

        # Mẫu hình nến
        kline_pattern = _detect_kline_pattern(klines)

        return TechnicalIndicators(
            ma5=ma5,
            ma10=ma10,
            ma20=ma20,
            ma60=ma60,
            macd_dif=macd_dif,
            macd_dea=macd_dea,
            macd_hist=macd_hist,
            macd_cross=macd_cross,
            macd_cross_days=macd_cross_days,
            rsi6=rsi6,
            rsi12=rsi12,
            rsi24=rsi24,
            kdj_k=kdj_k,
            kdj_d=kdj_d,
            kdj_j=kdj_j,
            kdj_cross=kdj_cross,
            boll_upper=boll_upper,
            boll_mid=boll_mid,
            boll_lower=boll_lower,
            boll_width=boll_width,
            volume_ratio=volume_ratio,
            volume_ma5=volume_ma5,
            volume_ma10=volume_ma10,
            volume_trend=volume_trend,
            change_5d=change_5d,
            change_20d=change_20d,
            amplitude=amplitude,
            amplitude_avg5=amplitude_avg5,
            atr=atr,
            atr_pct=atr_pct,
            support_s=support_s,
            support_m=support_m,
            support_l=support_l,
            resistance_s=resistance_s,
            resistance_m=resistance_m,
            resistance_l=resistance_l,
            support=support,
            resistance=resistance,
            kline_pattern=kline_pattern,
        )

    def get_kline_summary(self, symbol: str) -> dict:
        """获取 K 线摘要（用于 prompt 和前端展示）"""
        klines = self.get_klines(symbol, days=120)
        if not klines:
            return {"error": "无K线数据"}
        indicators = self.get_technical_indicators(klines=klines)

        # Diễn biến 5 phiên gần nhất
        recent_5 = klines[-5:] if len(klines) >= 5 else klines
        up_days = sum(
            1
            for i, k in enumerate(recent_5)
            if i > 0 and k.close > recent_5[i - 1].close
        )

        # Xác định xu hướng
        trend = "数据不足"
        if indicators.ma5 and indicators.ma10 and indicators.ma20:
            if indicators.ma5 > indicators.ma10 > indicators.ma20:
                trend = "多头排列"
            elif indicators.ma5 < indicators.ma10 < indicators.ma20:
                trend = "空头排列"
            else:
                trend = "均线交织"

        # Trạng thái MACD (chi tiết hơn)
        macd_status = "无数据"
        if indicators.macd_cross:
            days_str = (
                f"({indicators.macd_cross_days}日)"
                if indicators.macd_cross_days
                else ""
            )
            macd_status = f"{indicators.macd_cross}{days_str}"

        # Trạng thái RSI
        rsi_status = None
        if indicators.rsi6 is not None:
            if indicators.rsi6 > 80:
                rsi_status = "超买"
            elif indicators.rsi6 > 70:
                rsi_status = "偏强"
            elif indicators.rsi6 < 20:
                rsi_status = "超卖"
            elif indicators.rsi6 < 30:
                rsi_status = "偏弱"
            else:
                rsi_status = "中性"

        # Trạng thái KDJ
        kdj_status = None
        if indicators.kdj_k is not None and indicators.kdj_d is not None:
            if indicators.kdj_j is not None and indicators.kdj_j > 100:
                kdj_status = f"{indicators.kdj_cross}/超买"
            elif indicators.kdj_j is not None and indicators.kdj_j < 0:
                kdj_status = f"{indicators.kdj_cross}/超卖"
            else:
                kdj_status = indicators.kdj_cross

        # Trạng thái dải Bollinger
        boll_status = None
        last_close = klines[-1].close if klines else None
        if last_close and indicators.boll_upper and indicators.boll_lower:
            if last_close > indicators.boll_upper:
                boll_status = "突破上轨"
            elif last_close < indicators.boll_lower:
                boll_status = "跌破下轨"
            elif indicators.boll_width:
                if indicators.boll_width < 5:
                    boll_status = "收口窄幅"
                elif indicators.boll_width > 15:
                    boll_status = "开口放大"
                else:
                    boll_status = "正常波动"

        last_date = klines[-1].date if klines else None
        now = datetime.now(timezone.utc).isoformat()

        return {
            # meta
            "timeframe": "1d",
            "computed_at": now,
            "asof": last_date,
            "params": {
                "ma": [5, 10, 20, 60],
                "macd": {"fast": 12, "slow": 26, "signal": 9},
                "rsi": {"periods": [6, 12, 24]},
                "kdj": {"n": 9, "m1": 3, "m2": 3},
                "boll": {"period": 20, "num_std": 2},
                "support_resistance": {"windows": [5, 20, 60]},
            },
            "last_close": last_close,
            "recent_5_up": up_days,
            "trend": trend,
            # MACD
            "macd_status": macd_status,
            "macd_cross": indicators.macd_cross,
            "macd_cross_days": indicators.macd_cross_days,
            "macd_hist": indicators.macd_hist,
            # RSI
            "rsi6": indicators.rsi6,
            "rsi_status": rsi_status,
            # KDJ
            "kdj_k": indicators.kdj_k,
            "kdj_d": indicators.kdj_d,
            "kdj_j": indicators.kdj_j,
            "kdj_status": kdj_status,
            # Dải Bollinger
            "boll_upper": indicators.boll_upper,
            "boll_mid": indicators.boll_mid,
            "boll_lower": indicators.boll_lower,
            "boll_width": indicators.boll_width,
            "boll_status": boll_status,
            # Sức khối lượng
            "volume_ratio": indicators.volume_ratio,
            "volume_trend": indicators.volume_trend,
            # Đường trung bình
            "ma5": indicators.ma5,
            "ma10": indicators.ma10,
            "ma20": indicators.ma20,
            "ma60": indicators.ma60,
            # Biên độ
            "change_5d": indicators.change_5d,
            "change_20d": indicators.change_20d,
            # Biên dao động
            "amplitude": indicators.amplitude,
            "amplitude_avg5": indicators.amplitude_avg5,
            # Độ biến động (ATR)
            "atr": indicators.atr,
            "atr_pct": indicators.atr_pct,
            # Hỗ trợ / kháng cự nhiều tầng
            "support_s": indicators.support_s,
            "support_m": indicators.support_m,
            "support_l": indicators.support_l,
            "resistance_s": indicators.resistance_s,
            "resistance_m": indicators.resistance_m,
            "resistance_l": indicators.resistance_l,
            # Tương thích trường cũ
            "support": indicators.support,
            "resistance": indicators.resistance,
            # Mẫu hình nến
            "kline_pattern": indicators.kline_pattern,
        }
