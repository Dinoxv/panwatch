"""Các kiểu ngoại lệ của marketdata."""


class MarketDataError(Exception):
    """Lớp cơ sở cho mọi ngoại lệ của gói này."""


class VendorError(MarketDataError):
    """Một vendor lấy dữ liệu hỏng (Engine bắt rồi chuyển sang nguồn kế tiếp)."""
