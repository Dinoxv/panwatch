"""Cấu trúc dữ liệu tin tức + shim mỏng cho bộ thu thập gộp.

Phần lấy dữ liệu thật (tin cổ phiếu riêng lẻ của Xueqiu / tìm tin cổ phiếu riêng lẻ của
Đông Tài / công bố Đông Tài) đã gom vào gói marketdata (packages/marketdata), tệp này chỉ
giữ cấu trúc dữ liệu NewsItem mà bên tiêu thụ vẫn dùng, cùng một shim NewsCollector
chuyển tiếp vào gói, để bên tiêu thụ không phải sửa gì.
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class NewsItem:
    """Cấu trúc dữ liệu tin tức"""
    source: str           # "xueqiu" / "eastmoney_news" / "eastmoney"
    external_id: str      # ID duy nhất phía nguồn
    title: str
    content: str
    publish_time: datetime
    symbols: list[str] = field(default_factory=list)  # Mã cổ phiếu liên quan
    importance: int = 0   # Mức quan trọng 0-3
    url: str = ""         # Liên kết bài gốc


class NewsCollector:
    """Bộ thu thập tin tức gộp — shim mỏng, phần lấy dữ liệu/gộp/gộp trùng thật đã gom vào gói marketdata."""

    @classmethod
    def from_database(cls) -> "NewsCollector":
        """Cấu hình nay do DbConfigProvider trong gói đọc bảng DataSource khi cần, ở đây trả thẳng thực thể."""
        return cls()

    async def fetch_all(
        self,
        symbols: list[str] | None = None,
        since_hours: int = 2,
        symbol_names: dict[str, str] | None = None,
    ) -> list[NewsItem]:
        """
        Gộp tin tức của mọi nguồn tin đang bật (phần gộp/gộp trùng/sắp xếp đều làm bên trong gói marketdata).

        Args:
            symbols: danh sách mã cổ phiếu
            since_hours: lấy tin trong N giờ gần nhất (cửa sổ của nguồn dạng công bố do gói tự nới)
            symbol_names: ánh xạ mã cổ phiếu sang tên (tùy chọn, eastmoney_news tìm bằng tên hiệu quả hơn)

        Returns:
            danh sách tin xếp theo thời gian giảm dần
        """
        from src.platform.marketdata.marketdata_client import md_news

        return await asyncio.to_thread(md_news, symbols or [], since_hours, symbol_names)
