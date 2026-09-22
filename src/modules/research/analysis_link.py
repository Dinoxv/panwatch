"""Dựng liên kết tới trang nội bộ PanWatch: trang chi tiết phân tích chuyên sâu và tương tự.

Khóa thiết lập toàn cục: panwatch_base_url (địa chỉ truy cập công khai, dùng cho liên kết tuyệt đối tới trang chi tiết trong thông báo).
Cách đọc giống stock_link.py (AppSettings, thiếu thì lùi về mặc định).
"""

from __future__ import annotations

import logging

from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import AppSettings

logger = logging.getLogger(__name__)

SETTING_KEY = "panwatch_base_url"


def get_base_url() -> str:
    """Đọc địa chỉ truy cập công khai từ AppSettings (bỏ dấu gạch chéo ở cuối); chưa cấu hình / DB không dùng được thì trả chuỗi rỗng.

    Bọc thêm một lớp lưới hứng: lúc chạy unit test hoặc DB chưa khởi tạo (chưa có bảng
    app_settings), việc đọc thiết lập không được làm sập cả phần ánh xạ kết quả phân tích
    — đọc không ra thì hạ xuống chuỗi rỗng (không ghép liên kết chi tiết).
    """
    try:
        db = SessionLocal()
        try:
            row = db.query(AppSettings).filter(AppSettings.key == SETTING_KEY).first()
            val = (row.value if row and row.value else "").strip()
            return val.rstrip("/")
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001 — cơ sở dữ liệu chưa khởi tạo / thiếu bảng đều hạ cấp thành rỗng
        logger.debug(f"get_base_url 读取失败,降级为空: {e}")
        return ""


def analysis_detail_url(symbol: str, date: str, base_url: str = "") -> str:
    """URL trang chi tiết phân tích chuyên sâu: {base}/analysis/{symbol}/{date}.

    base_url chưa cấu hình (rỗng) thì trả chuỗi rỗng — bên gọi dựa vào đó để quyết định có ghép liên kết hay không.
    """
    if not base_url:
        base_url = get_base_url()
    if not base_url:
        return ""
    return f"{base_url}/analysis/{symbol}/{date}"


def analysis_detail_markdown(
    symbol: str, date: str, label: str = "📊 查看完整分析详情", base_url: str = ""
) -> str:
    """Liên kết Markdown [label](url); không có base_url thì trả chuỗi rỗng."""
    url = analysis_detail_url(symbol, date, base_url)
    if not url:
        return ""
    return f"[{label}]({url})"
