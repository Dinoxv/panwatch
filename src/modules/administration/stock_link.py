"""Công cụ sinh liên kết ngoài cho cổ phiếu: dựng URL trang giá theo mã, thị trường và nền tảng người dùng chọn.

Khóa cài đặt toàn cục: stock_link_platform (mặc định xueqiu)
"""

from __future__ import annotations

import logging

from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import AppSettings

logger = logging.getLogger(__name__)

# Các nền tảng được hỗ trợ {code: tên hiển thị}
PLATFORMS = {
    "xueqiu": "雪球",
}

DEFAULT_PLATFORM = "xueqiu"
SETTING_KEY = "stock_link_platform"


def get_platform() -> str:
    """Đọc mã nền tảng đang cấu hình từ AppSettings."""
    db = SessionLocal()
    try:
        row = db.query(AppSettings).filter(AppSettings.key == SETTING_KEY).first()
        return (row.value if row and row.value else DEFAULT_PLATFORM)
    finally:
        db.close()


def stock_url(symbol: str, market: str, platform: str = "") -> str:
    """Sinh URL trang giá của cổ phiếu.

    Args:
        symbol: mã cổ phiếu, ví dụ "002837", "AAPL", "00883"
        market: mã thị trường, ví dụ "CN", "US", "HK"
        platform: mã nền tảng, để rỗng thì đọc từ cài đặt toàn cục
    """
    if not platform:
        platform = get_platform()

    m = market.upper()

    if platform == "xueqiu":
        return _xueqiu_url(symbol, m)

    # Phương án dự phòng
    return _xueqiu_url(symbol, m)


def stock_link_markdown(symbol: str, market: str, platform: str = "") -> str:
    """Sinh liên kết cổ phiếu dạng Markdown: [002837.CN](https://xueqiu.com/S/SZ002837)"""
    code = f"{symbol}.{market}"
    url = stock_url(symbol, market, platform)
    return f"[{code}]({url})"


# ---------------------------------------------------------------------------
# Sinh URL cho từng nền tảng
# ---------------------------------------------------------------------------

def _xueqiu_url(symbol: str, market: str) -> str:
    if market == "US":
        return f"https://xueqiu.com/S/{symbol}"
    if market == "HK":
        return f"https://xueqiu.com/S/{symbol}"
    # CN cổ phiếu A
    from src.platform.marketdata.cn_symbol import get_cn_prefix
    prefix = get_cn_prefix(symbol, upper=True)
    return f"https://xueqiu.com/S/{prefix}{symbol}"
