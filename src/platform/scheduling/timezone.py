"""Tiện ích xử lý múi giờ - thống nhất cách lưu và hiển thị thời gian.

Múi giờ mặc định ghi đè được qua biến môi trường:
- TZ (khuyên dùng)

Không đặt thì mặc định Asia/Shanghai.
"""

from datetime import datetime, timezone
import os
from zoneinfo import ZoneInfo


def _get_app_tz() -> ZoneInfo:
    tz_name = os.environ.get("TZ") or os.environ.get("APP_TIMEZONE") or "Asia/Shanghai"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def utc_now() -> datetime:
    """Lấy thời gian UTC hiện tại (kèm thông tin múi giờ)"""
    return datetime.now(timezone.utc)


def beijing_now() -> datetime:
    """Lấy thời gian hiện tại theo múi giờ mặc định (giữ tên cũ theo lịch sử; kèm thông tin múi giờ)"""
    return datetime.now(_get_app_tz())


def to_utc(dt: datetime) -> datetime:
    """Đổi thời gian sang UTC"""
    if dt.tzinfo is None:
        # Coi thời gian không có múi giờ là theo múi giờ mặc định
        dt = dt.replace(tzinfo=_get_app_tz())
    return dt.astimezone(timezone.utc)


def to_beijing(dt: datetime) -> datetime:
    """Đổi thời gian sang múi giờ mặc định (giữ tên cũ theo lịch sử)"""
    if dt.tzinfo is None:
        # Coi thời gian không có múi giờ là UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_get_app_tz())


def format_beijing(dt: datetime, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Định dạng thành chuỗi theo múi giờ mặc định (giữ tên cũ theo lịch sử)"""
    return to_beijing(dt).strftime(fmt)


def to_iso_utc(dt: datetime) -> str:
    """Đổi thành chuỗi thời gian UTC định dạng ISO (kèm hậu tố Z)"""
    utc_dt = to_utc(dt)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def to_iso_with_tz(dt: datetime) -> str:
    """Đổi thành chuỗi định dạng ISO (kèm độ lệch múi giờ)"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
