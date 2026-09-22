"""Khẩu độ horizon tính theo phiên giao dịch (không theo ngày tự nhiên).

Chuỗi K-line chính là lịch giao dịch: cuối tuần, nghỉ lễ và phiên đình chỉ giao
dịch không được phép ăn vào horizon. Trước đây `signal_date + timedelta(days=N)`
làm độ dài cửa sổ đo phụ thuộc vào thứ trong tuần mà tín hiệu tình cờ phát ra —
tín hiệu phát thứ Sáu với horizon 1 ngày rơi vào Chủ nhật rồi bị lùi về chính
giá đóng cửa thứ Sáu, tức đo được 0 phiên.
"""

from datetime import date

from src.platform.marketdata.bars import (
    HORIZON_UNIT_CALENDAR_LEGACY,
    HORIZON_UNIT_TRADING_DAYS,
    bar_after_n_trading_days,
    base_index_on_or_before,
    close_after_n_trading_days,
    close_on_or_before,
    close_series,
)


class _Bar:
    """Cây nến tối giản, chỉ mang ngày phiên và giá đóng cửa."""

    def __init__(self, day: str, close: float):
        self.date = day
        self.close = close


# Thứ Sáu 2026-01-02, rồi nghỉ cuối tuần, mở lại thứ Hai 2026-01-05.
_WEEK = [
    _Bar("2026-01-02", 10.0),  # thứ Sáu — phiên phát tín hiệu
    _Bar("2026-01-05", 11.0),  # thứ Hai — phiên +1
    _Bar("2026-01-06", 12.0),  # thứ Ba  — phiên +2
    _Bar("2026-01-07", 13.0),  # thứ Tư  — phiên +3
]


def test_friday_signal_horizon_1_skips_the_weekend():
    """Tín hiệu thứ Sáu, horizon 1 phiên → giá đóng cửa thứ Hai, không phải thứ Sáu."""
    rows = close_series(_WEEK)
    base = base_index_on_or_before(rows, date(2026, 1, 2))
    assert base == 0

    bar = bar_after_n_trading_days(rows, base, 1)
    assert bar is not None
    day, close = bar
    assert day == date(2026, 1, 5)
    assert close == 11.0


def test_calendar_arithmetic_would_have_measured_zero_sessions():
    """Chốt lại đúng hành vi sai của khẩu độ cũ, để không ai vô tình khôi phục nó.

    Thứ Sáu + 1 ngày tự nhiên = thứ Bảy; "giá đóng cửa gần nhất không muộn hơn
    thứ Bảy" lùi về chính thứ Sáu → đo được 0 phiên, lợi nhuận luôn bằng 0.
    """
    legacy = close_on_or_before(_WEEK, date(2026, 1, 3))  # thứ Bảy
    assert legacy == 10.0  # = chính giá đóng cửa của phiên phát tín hiệu

    fixed = close_after_n_trading_days(_WEEK, date(2026, 1, 2), 1)
    assert fixed == 11.0
    assert fixed != legacy


def test_horizon_counts_sessions_not_calendar_days():
    """horizon 3 = phiên thứ 3, dù đã trôi qua 5 ngày tự nhiên."""
    assert close_after_n_trading_days(_WEEK, date(2026, 1, 2), 3) == 13.0


def test_signal_dated_on_a_non_trading_day_anchors_to_last_session():
    """Tín hiệu phát tối thứ Bảy vẫn lấy phiên thứ Sáu làm mốc gốc.

    Lịch làm mới cơ hội chạy lúc 22:00, nên `snapshot_date` có thể rơi vào ngày
    không có phiên nào.
    """
    rows = close_series(_WEEK)
    base = base_index_on_or_before(rows, date(2026, 1, 3))  # thứ Bảy
    assert base == 0
    assert bar_after_n_trading_days(rows, base, 1)[1] == 11.0


def test_insufficient_bars_is_not_due_rather_than_missing_price():
    """Chuỗi chưa đủ phiên → None, nghĩa là CHƯA TỚI HẠN chốt.

    Người gọi phải phân biệt với "thiếu dữ liệu giá": ghi sổ lúc này sẽ tạo ra
    một kết quả hậu kiểm non và bơm nhiễu vào vòng điều chỉnh trọng số.
    """
    rows = close_series(_WEEK)
    base = base_index_on_or_before(rows, date(2026, 1, 2))
    assert bar_after_n_trading_days(rows, base, 3) is not None
    assert bar_after_n_trading_days(rows, base, 4) is None  # chuỗi chỉ có 3 phiên sau


def test_suspended_session_does_not_consume_horizon():
    """Mã bị đình chỉ giao dịch: phiên nghỉ không có nến nên không ăn vào horizon."""
    bars = [
        _Bar("2026-03-02", 20.0),
        # 03-03 .. 03-20 đình chỉ giao dịch, không có nến nào
        _Bar("2026-03-23", 24.0),
    ]
    assert close_after_n_trading_days(bars, date(2026, 3, 2), 1) == 24.0


def test_unsorted_and_malformed_bars_are_tolerated():
    """Nến đảo thứ tự vẫn được sắp lại; nến thiếu giá/ngày bị loại."""
    bars = [
        _Bar("2026-01-06", 12.0),
        _Bar("2026-01-02", 10.0),
        _Bar("không-phải-ngày", 99.0),
        _Bar("2026-01-05", None),
        _Bar("2026-01-05", 11.0),
    ]
    rows = close_series(bars)
    assert [r[1] for r in rows] == [10.0, 11.0, 12.0]


def test_horizon_unit_labels_are_stable():
    """Nhãn ghi vào cột horizon_unit phải khớp với giá trị v120 đã dùng."""
    assert HORIZON_UNIT_TRADING_DAYS == "trading_days"
    assert HORIZON_UNIT_CALENDAR_LEGACY == "calendar_days_legacy"


def test_backtest_horizon_return_uses_the_same_unit():
    """horizon_return tồn tại để đối chiếu chéo — phải cùng khẩu độ phiên giao dịch."""
    from src.modules.strategy.backtest.data_adapter import PriceBar
    from src.modules.strategy.backtest.engine import Signal, horizon_return

    bars = [
        PriceBar(date="2026-01-02", open=10, high=10, low=10, close=10.0, volume=0),
        PriceBar(date="2026-01-05", open=11, high=11, low=11, close=11.0, volume=0),
        PriceBar(date="2026-01-06", open=12, high=12, low=12, close=12.0, volume=0),
    ]
    sig = Signal("X", "CN", "2026-01-02", entry_price=10.0)

    # 1 phiên sau thứ Sáu là thứ Hai → +10%, chứ không phải 0% như khẩu độ cũ.
    assert abs(horizon_return(sig, bars, horizon_days=1) - 10.0) < 1e-6
    assert abs(horizon_return(sig, bars, horizon_days=2) - 20.0) < 1e-6
    # Chưa đủ phiên để chốt.
    assert horizon_return(sig, bars, horizon_days=3) is None
