"""Số học phiên giao dịch trên chuỗi K-line.

Bản thân chuỗi K-line **chính là lịch giao dịch**: phiên nghỉ cuối tuần, nghỉ lễ
và phiên bị đình chỉ giao dịch đều không xuất hiện trong chuỗi, nên đếm tiến N
phần tử trong chuỗi cho ra đúng "N phiên sau", không cần tra lịch riêng và không
cần dữ liệu nghỉ lễ cho từng thị trường.

Đây là lý do mọi phép đo hậu kiểm (horizon) phải đếm theo phiên chứ không theo
ngày tự nhiên: `signal_date + timedelta(days=N)` làm độ dài cửa sổ đo phụ thuộc
vào thứ trong tuần mà tín hiệu tình cờ phát ra — tín hiệu phát thứ Sáu với
horizon 1 ngày sẽ rơi vào Chủ nhật và bị lùi về chính giá đóng cửa thứ Sáu, tức
đo được 0 phiên.

Module này chỉ làm số học thuần trên dữ liệu giá, không biết gì về nghiệp vụ.
"""

from __future__ import annotations

from datetime import date, datetime

# Nhãn ghi trong cột `horizon_unit` của các bảng hậu kiểm.
HORIZON_UNIT_TRADING_DAYS = "trading_days"
HORIZON_UNIT_CALENDAR_LEGACY = "calendar_days_legacy"


def parse_day(value) -> date | None:
    """Chuẩn hóa ngày của một cây nến về `date`; không nhận dạng được thì trả None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except Exception:
            continue
    return None


def close_series(klines: list) -> list[tuple[date, float]]:
    """K-line → [(ngày phiên, giá đóng cửa)] đã sắp xếp tăng dần.

    Cây nến thiếu ngày hoặc thiếu giá đóng cửa bị loại, vì chúng không đại diện
    cho một phiên giao dịch dùng đếm được.
    """
    rows: list[tuple[date, float]] = []
    for k in klines or []:
        day = parse_day(getattr(k, "date", None))
        close = getattr(k, "close", None)
        if day is None or close is None:
            continue
        try:
            rows.append((day, float(close)))
        except (TypeError, ValueError):
            continue
    rows.sort(key=lambda item: item[0])
    return rows


def base_index_on_or_before(rows: list[tuple[date, float]], target: date) -> int | None:
    """Chỉ số của phiên gần nhất không muộn hơn `target` — mốc gốc để đếm tiến.

    Dùng "không muộn hơn" thay vì "đúng ngày" vì tín hiệu có thể phát vào ngày
    nghỉ (ví dụ lịch làm mới cơ hội chạy 22:00 tối thứ Sáu): khi đó phiên gốc là
    phiên thứ Sáu vừa đóng cửa.
    """
    found: int | None = None
    for index, (day, _) in enumerate(rows):
        if day <= target:
            found = index
        else:
            break
    return found


def close_on_or_before(klines: list, target: date) -> float | None:
    """Giá đóng cửa của phiên gần nhất không muộn hơn `target`.

    Dùng cho giá gốc (base price) khi tín hiệu không kèm dải vào lệnh — đây là
    phép tra theo ngày, không phải phép đếm horizon.
    """
    rows = close_series(klines)
    index = base_index_on_or_before(rows, target)
    return None if index is None else rows[index][1]


def bar_after_n_trading_days(
    rows: list[tuple[date, float]],
    base_index: int,
    horizon: int,
) -> tuple[date, float] | None:
    """Phiên thứ `horizon` tính từ phiên gốc (đếm nghiêm ngặt về phía sau).

    Trả None khi chuỗi chưa đủ phiên — nghĩa là horizon **chưa tới hạn chốt**,
    không phải "thiếu dữ liệu giá". Người gọi phải phân biệt hai trường hợp này,
    nếu không sẽ ghi sớm một kết quả hậu kiểm chưa chín.
    """
    if base_index < 0:
        return None
    target = base_index + max(1, int(horizon))
    if target >= len(rows):
        return None
    return rows[target]


def close_after_n_trading_days(
    klines: list,
    base_day: date,
    horizon: int,
) -> float | None:
    """Giá đóng cửa sau `horizon` phiên kể từ `base_day`. None = chưa đủ phiên."""
    rows = close_series(klines)
    index = base_index_on_or_before(rows, base_day)
    if index is None:
        return None
    bar = bar_after_n_trading_days(rows, index, horizon)
    return None if bar is None else bar[1]
