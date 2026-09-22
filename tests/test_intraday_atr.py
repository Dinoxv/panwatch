"""ATR 自适应异动检测相关测试。

覆盖:
1. kline_collector._calculate_atr / get_technical_indicators 的 atr / atr_pct 字段
2. intraday_event_gate.is_abnormal_move 自适应异动判定(含固定阈值兜底)
3. intraday_monitor.build_prompt 注入 ATR 与自适应异动规则文案
"""

from __future__ import annotations

from src.platform.marketdata.collectors.kline_collector import (
    KlineData,
    _calculate_atr,
)


def _mk(o: float, h: float, l: float, c: float) -> KlineData:
    """构造一根 K 线(成交量固定为 1000,不影响 ATR)。"""
    return KlineData(date="2026-06-20", open=o, high=h, low=l, close=c, volume=1000)


def test_calculate_atr_known_series() -> None:
    """ATR — 已知小样本 OHLC 手算值匹配(TR 简单均值)。"""
    # 6 cây nến, period=3.
    # Chuỗi giá đóng cửa: 10, 11, 10, 12, 11, 13
    klines = [
        _mk(10, 10.5, 9.5, 10.0),  # Cây đầu tiên (không có giá đóng cửa phiên trước nên TR không tính)
        _mk(10, 12.0, 10.0, 11.0),  # TR = max(2, |12-10|, |10-10|) = 2
        _mk(11, 11.5, 9.0, 10.0),  # TR = max(2.5, |11.5-11|, |9-11|) = 2.5
        _mk(10, 12.5, 10.0, 12.0),  # TR = max(2.5, |12.5-10|, |10-10|) = 2.5
        _mk(12, 12.0, 10.5, 11.0),  # TR = max(1.5, |12-12|, |10.5-12|) = 1.5
        _mk(11, 13.5, 11.0, 13.0),  # TR = max(2.5, |13.5-11|, |11-11|) = 2.5
    ]
    # period=3 -> lấy 3 giá trị TR gần nhất: [2,5; 1,5; 2,5] -> trung bình = 2,1666...
    atr = _calculate_atr(klines, period=3)
    assert atr is not None
    assert abs(atr - (2.5 + 1.5 + 2.5) / 3) < 1e-9


def test_calculate_atr_insufficient_data_returns_none() -> None:
    """ATR — K 线不足(<=period)时返回 None,不抛异常。"""
    klines = [_mk(10, 11, 9, 10), _mk(10, 11, 9, 10)]
    # Phải có period+1 cây nến mới tính ra được period giá trị TR
    assert _calculate_atr(klines, period=14) is None
    assert _calculate_atr([], period=14) is None
    # Đúng bằng period cây cũng chưa đủ (chỉ ra được period-1 giá trị TR)
    assert _calculate_atr(klines, period=2) is None


def test_get_technical_indicators_includes_atr_and_pct() -> None:
    """技术指标 — get_technical_indicators 返回 atr 与 atr_pct(=atr/收盘*100)。"""
    from src.platform.marketdata.collectors.kline_collector import KlineCollector

    # ATR mặc định period=14 nên cần từ 15 cây nến trở lên mới tính được.
    klines = [_mk(10 + i * 0.1, 11 + i * 0.1, 9 + i * 0.1, 10 + i * 0.1) for i in range(14)]
    klines.append(_mk(11, 13.5, 11.0, 20.0))  # Giá đóng cửa mới nhất = 20, để dễ kiểm chứng atr_pct
    collector = KlineCollector(MarketStub())
    ind = collector.get_technical_indicators(klines=klines)
    assert ind.atr is not None
    assert ind.atr_pct is not None
    # atr_pct = round(atr / latest_close * 100, 2)
    assert abs(ind.atr_pct - round(ind.atr / 20.0 * 100, 2)) < 1e-9


def test_get_technical_indicators_atr_none_when_insufficient() -> None:
    """技术指标 — K 线过少时 atr / atr_pct 为 None,其余指标仍可返回。"""
    from src.platform.marketdata.collectors.kline_collector import KlineCollector

    klines = [_mk(10, 11, 9, 10)]
    collector = KlineCollector(MarketStub())
    ind = collector.get_technical_indicators(klines=klines)
    assert ind.atr is None
    assert ind.atr_pct is None


class MarketStub:
    """KlineCollector 仅在联网取数时用到 market;本测试只传 klines,占位即可。"""

    def __init__(self) -> None:
        from src.platform.marketdata.models import MarketCode

        self.value = MarketCode.CN


# ── is_abnormal_move (xác định biến động bất thường thích ứng) ───────────


def test_is_abnormal_move_beyond_k_times_atr() -> None:
    """自适应异动 — 涨跌幅超过 k×ATR% 判为异动。"""
    from src.modules.strategy.intraday_event_gate import is_abnormal_move

    # atr_pct=2, k=1,5 -> ngưỡng=3,0; change=4 phải là bất thường
    assert is_abnormal_move(change_pct=4.0, atr_pct=2.0, k=1.5) is True
    assert is_abnormal_move(change_pct=-4.0, atr_pct=2.0, k=1.5) is True


def test_is_abnormal_move_within_band_is_normal() -> None:
    """自适应异动 — 涨跌幅在 k×ATR% 以内判为正常波动。"""
    from src.modules.strategy.intraday_event_gate import is_abnormal_move

    # atr_pct=2, k=1,5 -> ngưỡng=3,0; change=2 nằm trong dải
    assert is_abnormal_move(change_pct=2.0, atr_pct=2.0, k=1.5) is False
    assert is_abnormal_move(change_pct=-2.5, atr_pct=2.0, k=1.5) is False


def test_is_abnormal_move_falls_back_to_fixed_threshold() -> None:
    """自适应异动 — atr_pct 为 None/0 时回退到固定阈值。"""
    from src.modules.strategy.intraday_event_gate import is_abnormal_move

    # Không có ATR -> dùng ngưỡng cố định 3,0
    assert is_abnormal_move(change_pct=4.0, atr_pct=None, fixed_threshold=3.0) is True
    assert is_abnormal_move(change_pct=2.0, atr_pct=None, fixed_threshold=3.0) is False
    # atr_pct=0 cũng lùi về như vậy
    assert is_abnormal_move(change_pct=4.0, atr_pct=0.0, fixed_threshold=3.0) is True
    assert is_abnormal_move(change_pct=2.0, atr_pct=0.0, fixed_threshold=3.0) is False


def test_is_abnormal_move_default_k() -> None:
    """自适应异动 — k 有默认值(1.5)。"""
    from src.modules.strategy.intraday_event_gate import is_abnormal_move

    # Mặc định k=1,5, atr_pct=2 -> ngưỡng=3,0
    assert is_abnormal_move(change_pct=3.5, atr_pct=2.0) is True
    assert is_abnormal_move(change_pct=2.5, atr_pct=2.0) is False


# ── build_prompt của intraday_monitor nạp ATR / quy tắc thích ứng ────────


def _build_intraday_prompt_with_atr(atr_pct):
    """构造最小 AgentContext/data,跑 build_prompt,返回 user_content。"""
    from src.modules.automation.intraday_monitor import IntradayMonitorAgent
    from src.platform.marketdata.models import MarketCode, StockData

    stock = StockData(
        symbol="000001",
        name="平安银行",
        market=MarketCode.CN,
        current_price=20.0,
        change_pct=5.0,
        change_amount=1.0,
        open_price=19.0,
        high_price=20.5,
        low_price=18.8,
        prev_close=19.0,
        volume=10000,
        turnover=200000,
    )

    class _Portfolio:
        total_available_funds = 100000.0
        accounts: list = []

        def get_positions_for_stock(self, symbol):
            return []

    class _Ctx:
        portfolio = _Portfolio()

    data = {
        "stock_data": stock,
        "kline_summary": {"atr": 0.6, "atr_pct": atr_pct, "trend": "多头排列"},
        "symbol_context": {},
    }
    agent = IntradayMonitorAgent(price_alert_threshold=3.0)
    _system, user_content = agent.build_prompt(data, _Ctx())
    return user_content


def test_build_prompt_injects_atr_and_adaptive_rule() -> None:
    """盘中监控 — prompt 注入 ATR% 与自适应价格异动阈值文案。"""
    content = _build_intraday_prompt_with_atr(atr_pct=2.0)
    # Giá trị ATR xuất hiện trong phần tóm tắt kỹ thuật
    assert "ATR" in content
    # Ngưỡng thích ứng: max(ngưỡng cố định 3,0; 1,5×ATR% = 3,0) -> câu chữ thể hiện tính thích ứng
    assert "自适应" in content
    # change_pct=5,0 vượt ngưỡng thích ứng -> phải đánh dấu là đã kích hoạt
    assert "触发" in content


def test_build_prompt_falls_back_when_atr_missing() -> None:
    """盘中监控 — atr_pct 缺失时退回固定阈值,prompt 仍正常生成。"""
    content = _build_intraday_prompt_with_atr(atr_pct=None)
    # Ngưỡng cố định vẫn được hiển thị
    assert "3.0%" in content or "3.0" in content
    # change_pct=5,0 > ngưỡng cố định 3,0 -> kích hoạt
    assert "触发" in content
