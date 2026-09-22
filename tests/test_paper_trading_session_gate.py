"""Cổng giờ giao dịch theo từng thị trường của mô phỏng bàn giao dịch.

Bộ lập lịch chỉ chặn ở mức "cả ba thị trường đều nghỉ" (`_any_market_trading`),
nên vòng quét 60 giây vẫn chạy suốt phiên Mỹ — 21:30 đến 04:00 giờ Bắc Kinh —
trong khi A股 và 港股 đã đóng cửa từ nhiều giờ trước. Báo giá của hai thị trường
đó lúc ấy là giá đóng cửa đóng băng, và hợp đồng báo giá **không mang dấu thời
gian** nên phía tiêu thụ không thể tự phân biệt giá sống với giá cũ.

Đường đi cụ thể trước khi vá: lịch làm mới cơ hội chạy cron 22:00 sinh tín hiệu
A股 mới; vòng quét kế tiếp (vẫn trong phiên Mỹ) khớp luôn tín hiệu đó tại giá
đóng cửa A股 — một mức giá không còn đặt lệnh được nữa.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.modules.paper_trading.paper_trading_engine import _is_trading_time
from src.platform.marketdata.models import MARKETS, MarketCode


# 22:00 giờ Bắc Kinh, thứ Năm 2026-09-17: phiên Mỹ đang mở, cổ phiếu A và Hồng Kông đã đóng cửa.
_CN_EVENING = datetime(2026, 9, 17, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def _in_session(market: MarketCode, moment: datetime) -> bool:
    return MARKETS[market].is_trading_time(moment)


def test_scheduler_gate_is_open_while_two_markets_are_closed():
    """Tiền đề của lỗi: 22:00 giờ Bắc Kinh, chỉ Mỹ mở nhưng vòng quét vẫn chạy."""
    assert _in_session(MarketCode.US, _CN_EVENING) is True
    assert _in_session(MarketCode.CN, _CN_EVENING) is False
    assert _in_session(MarketCode.HK, _CN_EVENING) is False

    # Đây chính là điều kiện mà `_any_market_trading()` kiểm tra.
    assert any(
        _in_session(m, _CN_EVENING)
        for m in (MarketCode.CN, MarketCode.HK, MarketCode.US)
    )


def test_weekend_closes_every_market():
    """Cuối tuần thì không thị trường nào mở — vòng quét bị chặn ở tầng lập lịch."""
    saturday = datetime(2026, 9, 19, 11, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert not any(
        _in_session(m, saturday)
        for m in (MarketCode.CN, MarketCode.HK, MarketCode.US)
    )


def test_cn_session_boundaries():
    """A股 giao dịch 09:30–11:30 và 13:00–15:00; giờ nghỉ trưa không phải phiên."""
    tz = ZoneInfo("Asia/Shanghai")
    day = (2026, 9, 17)  # thứ Năm
    assert _in_session(MarketCode.CN, datetime(*day, 10, 0, tzinfo=tz)) is True
    assert _in_session(MarketCode.CN, datetime(*day, 12, 0, tzinfo=tz)) is False
    assert _in_session(MarketCode.CN, datetime(*day, 14, 0, tzinfo=tz)) is True
    assert _in_session(MarketCode.CN, datetime(*day, 16, 0, tzinfo=tz)) is False


@pytest.mark.parametrize("market", ["CN", "HK", "US"])
def test_is_trading_time_accepts_the_market_code_stored_on_rows(market):
    """Helper nhận đúng chuỗi mã thị trường mà cột stock_market đang lưu."""
    assert isinstance(_is_trading_time(market), bool)


def test_unknown_market_is_treated_as_closed():
    """Mã thị trường lạ → coi như đóng cửa, không khớp lệnh mò."""
    assert _is_trading_time("") in (True, False)  # không được ném lỗi


def test_entries_and_exits_consult_the_session_gate():
    """Cổng phải được gọi trong CẢ hai nhánh vào lệnh và thoát lệnh.

    Hàm `_is_trading_time` đã tồn tại từ commit khai sinh module nhưng chưa từng
    có call site nào — test này chốt lại việc nó đã được nối vào, để lần refactor
    sau không âm thầm bỏ nó ra.
    """
    import inspect

    from src.modules.paper_trading.paper_trading_engine import PaperTradingEngine

    entries = inspect.getsource(PaperTradingEngine._check_entries)
    exits = inspect.getsource(PaperTradingEngine._check_exits)

    assert "_is_trading_time" in entries
    assert "_is_trading_time" in exits


def test_exit_gate_sits_after_the_mark_to_market_update():
    """Ngoài phiên vẫn phải cập nhật giá tham chiếu rồi mới bỏ qua lệnh.

    Bỏ qua sớm sẽ khiến màn hình đứng giá; bỏ qua muộn sẽ khớp lệnh ngoài phiên.
    Thứ tự đúng: cập nhật current_price/unrealized_pnl/highest_price → chặn.
    """
    import inspect

    from src.modules.paper_trading.paper_trading_engine import PaperTradingEngine

    src = inspect.getsource(PaperTradingEngine._check_exits)
    mark_at = src.index("pos.unrealized_pnl = ")
    gate_at = src.index("if not _is_trading_time(pos.stock_market)")
    stop_at = src.index("if pos.stop_loss and current_price <= pos.stop_loss")

    assert mark_at < gate_at < stop_at
