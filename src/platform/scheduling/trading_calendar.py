"""Lịch giao dịch: trả lời câu hỏi «ngày này có mở cửa không».

Bổ sung cho `MarketDef.is_trading_time()` (vốn trả lời «lúc này có đang trong phiên
không») — các tác vụ định giờ như kế hoạch trước phiên, tóm tắt cuối ngày vốn dĩ xảy ra
ngoài giờ giao dịch, nên chỉ canh được bằng «có phải phiên giao dịch không»; canh bằng
khoảng giờ sẽ chặn chết chúng vĩnh viễn.

Nguồn dữ liệu
- **Cổ phiếu A**: lịch giao dịch của akshare (`tool_trade_date_hist_sina`), có cả nghỉ lễ chính thức, đáng tin.
  Kết quả đệm trong bộ nhớ, `refresh()` cập nhật (hâm nóng lúc khởi động + làm mới mỗi rạng sáng).
- **Cổ phiếu HK / Mỹ**: không có nguồn lịch công khai tương đương, nên chỉ xét cuối tuần
  (hạ cấp một cách trung thực, không giả vờ hỗ trợ nghỉ lễ).

Nguyên tắc hạ cấp
Không lấy được lịch thì lùi về «chỉ xét cuối tuần» — thà gửi dư một thông báo còn hơn xét
nhầm phiên giao dịch thành ngày nghỉ. Gửi thiếu một bản là đáng tiếc, sót cả một ngày là sự cố.

An toàn khi chạy song song
Giao diện đồng bộ chỉ đọc đệm trong bộ nhớ, **không bao giờ phát yêu cầu mạng**; phần kéo
qua mạng gom vào `refresh()` (bên trong dùng `asyncio.to_thread`) và `refresh_blocking()`,
để khỏi chặn vòng lặp sự kiện.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Tập phiên giao dịch của cổ phiếu A; None = chưa nạp hoặc nạp thất bại (lúc đó hạ cấp về chỉ xét cuối tuần)
_CN_TRADING_DATES: frozenset[date] | None = None
# Khoảng thời gian mà lịch phủ, dùng để biết ngày truy vấn có nằm trong vùng đáng tin không (sang năm mới mà chưa làm mới thì sẽ vượt ra ngoài)
_CN_RANGE: tuple[date, date] | None = None

_FALLBACK_TZ = "Asia/Shanghai"


def reset_cache() -> None:
    """Xóa sạch đệm lịch (dùng khi đổi cấu hình hoặc khi test)."""
    global _CN_TRADING_DATES, _CN_RANGE
    _CN_TRADING_DATES = None
    _CN_RANGE = None


def _fetch_cn_trading_dates() -> frozenset[date]:
    """Kéo lịch giao dịch cổ phiếu A theo kiểu chặn. Chỉ `refresh_blocking()` gọi."""
    import akshare as ak

    df = ak.tool_trade_date_hist_sina()
    out: set[date] = set()
    for raw in df["trade_date"]:
        if isinstance(raw, datetime):
            out.add(raw.date())
        elif isinstance(raw, date):
            out.add(raw)
        else:
            out.add(date.fromisoformat(str(raw)[:10]))
    return frozenset(out)


def refresh_blocking() -> bool:
    """Làm mới lịch giao dịch cổ phiếu A một cách đồng bộ. Trả về thành công hay không; hỏng thì không ném lỗi (giữ hành vi hạ cấp)."""
    global _CN_TRADING_DATES, _CN_RANGE
    try:
        dates = _fetch_cn_trading_dates()
    except Exception as e:
        logger.warning("[交易日历] A股日历拉取失败,降级为只判周末: %s", e)
        return False
    if not dates:
        logger.warning("[交易日历] A股日历为空,降级为只判周末")
        return False
    _CN_TRADING_DATES = dates
    _CN_RANGE = (min(dates), max(dates))
    logger.info(
        "[交易日历] A股日历已加载: %s 个交易日 (%s ~ %s)",
        len(dates),
        _CN_RANGE[0],
        _CN_RANGE[1],
    )
    return True


async def refresh() -> bool:
    """Làm mới lịch bất đồng bộ (qua thread pool, không chặn vòng lặp sự kiện)."""
    return await asyncio.to_thread(refresh_blocking)


def _to_market_code(market):
    """Chuẩn hóa MarketCode / chuỗi về MarketCode; không nhận ra thì trả None."""
    from src.platform.marketdata.models import MarketCode

    if isinstance(market, MarketCode):
        return market
    try:
        return MarketCode(str(market).strip().upper())
    except ValueError:
        return None


def _market_tz(code) -> ZoneInfo:
    from src.platform.marketdata.models import MARKETS

    md = MARKETS.get(code) if code else None
    return md.get_tz() if md else ZoneInfo(_FALLBACK_TZ)


def _now_in_market_tz(code) -> datetime:
    """Thời gian hiện tại theo múi giờ của thị trường đó. Tách thành hàm riêng cho dễ tiêm vào lúc test."""
    return datetime.now(_market_tz(code))


def _resolve_date(code, d: date | datetime | None) -> date:
    """Chuẩn hóa tham số về «ngày địa phương của thị trường đó»."""
    if d is None:
        return _now_in_market_tz(code).date()
    if isinstance(d, datetime):
        if d.tzinfo is not None:
            d = d.astimezone(_market_tz(code))
        return d.date()
    return d


def is_trading_day(market, d: date | datetime | None = None) -> bool:
    """Một ngày nào đó của thị trường cho trước có mở cửa không.

    Args:
        market: `MarketCode` hoặc chuỗi mã thị trường (CN/HK/US).
        d: ngày mục tiêu; `None` nghĩa là hôm nay theo múi giờ của thị trường đó.
           `datetime` có múi giờ sẽ được quy về múi giờ thị trường rồi mới lấy ngày.
    """
    from src.platform.marketdata.models import MarketCode

    code = _to_market_code(market)
    target = _resolve_date(code, d)

    # Cuối tuần: cả ba thị trường đều đóng. Không phụ thuộc gì, luôn đúng, nên đặt lên đầu.
    if target.weekday() >= 5:
        return False

    # Cổ phiếu A: khi lịch đã nạp và có phủ ngày đó thì xét theo lịch (gồm cả nghỉ lễ theo quy định).
    if code == MarketCode.CN and _CN_TRADING_DATES and _CN_RANGE:
        if _CN_RANGE[0] <= target <= _CN_RANGE[1]:
            return target in _CN_TRADING_DATES
        logger.debug("[交易日历] %s 超出A股日历覆盖范围,降级为只判周末", target)

    # Cổ phiếu Hồng Kông / Mỹ, lịch thiếu, hoặc ngày vượt vùng phủ: chỉ xét cuối tuần.
    return True


def any_market_trading_day(d: date | datetime | None = None) -> bool:
    """Chỉ cần một trong CN/HK/US là phiên giao dịch thì `True`. Mọi thị trường nghỉ (như cuối tuần) thì trả `False`."""
    from src.platform.marketdata.models import MarketCode

    return any(
        is_trading_day(m, d) for m in (MarketCode.CN, MarketCode.HK, MarketCode.US)
    )
