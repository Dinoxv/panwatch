"""Đấu nối PanWatch ↔ marketdata: cổng cấu hình DB + đơn nhất + tầng tương thích báo giá có cổng chặn bằng flag.

- DbConfigProvider: ánh xạ bảng DataSource thành SourceConfig của marketdata (hiện thực cổng ConfigProvider).
- get_market_data(): đơn nhất ở cấp tiến trình (vendor không trạng thái + cổng cấu hình tra DB tại chỗ).
- md_quote_rows(): đổi MarketData.quotes của gói mới sang dict, trả về list[dict] (cùng hình dạng đầu ra với orchestrator cũ).
- md_news()/md_news_by_keyword(): đổi MarketData.news/news_by_keyword của gói mới sang NewsItem của host.
"""

from __future__ import annotations

import logging

from marketdata import MarketData, Quote, SourceConfig

logger = logging.getLogger(__name__)


class DbConfigProvider:
    """Hiện thực cổng ConfigProvider: đọc các nguồn đang bật của một loại từ bảng DataSource theo priority."""

    def _query_rows(self, datatype: str) -> list:
        from src.platform.persistence.database import SessionLocal
        from src.platform.persistence.models import DataSource

        db = SessionLocal()
        try:
            return (
                db.query(DataSource)
                .filter(DataSource.type == datatype, DataSource.enabled == True)  # noqa: E712
                .order_by(DataSource.priority)
                .all()
            )
        finally:
            db.close()

    def sources_for(self, datatype: str, market: str | None) -> list[SourceConfig]:
        market_code = (market or "").strip().upper()
        rows = self._query_rows(datatype)
        has_us_fallback = any(
            row.provider in {"stooq", "yahoo"} for row in rows
        )
        sources = []
        for row in rows:
            # Endpoint cổ phiếu Mỹ của Tencent luôn trả 501 ở đường ra mạng hiện tại; A / Hồng Kông vẫn giữ Tencent làm nguồn chính.
            if (
                datatype == "kline"
                and market_code == "US"
                and row.provider == "tencent"
                and has_us_fallback
            ):
                continue
            sources.append(
                SourceConfig(
                    vendor=row.provider,
                    priority=row.priority,
                    enabled=True,
                    config=row.config or {},
                    supports_batch=bool(row.supports_batch),
                )
            )
        return sources


_md: MarketData | None = None


def get_market_data() -> MarketData:
    """Đơn nhất ở cấp tiến trình. Vendor không trạng thái, cấu hình tra DB tại chỗ, nên không cần móc làm mất hiệu lực."""
    global _md
    if _md is None:
        _md = MarketData(config=DbConfigProvider())
    return _md


def reset_market_data() -> None:
    """Đặt lại đơn nhất khi test hoặc khi nạp nóng."""
    global _md
    _md = None


def _quote_to_row(q: Quote) -> dict:
    """marketdata.Quote → dict cùng hình dạng với orchestrator cũ."""
    return {
        "symbol": q.symbol,
        "name": q.name,
        "market": q.market,
        "current_price": q.current_price,
        "change_pct": q.change_pct,
        "change_amount": q.change_amount,
        "prev_close": q.prev_close,
        "open_price": q.open_price,
        "high_price": q.high_price,
        "low_price": q.low_price,
        "volume": q.volume,
        "turnover": q.turnover,
        "turnover_rate": q.turnover_rate,
        "volume_ratio": q.volume_ratio,
        "pe_ratio": q.pe_ratio,
        "circulating_market_value": q.circulating_market_value,
        "total_market_value": q.total_market_value,
    }


def md_quote_rows(symbols: list[str], market: str) -> list[dict]:
    """Báo giá hàng loạt, trả về list[dict] (cùng hình dạng đầu ra với orchestrator cũ).

    Hàm đồng bộ; bên gọi async dùng `await asyncio.to_thread(md_quote_rows, ...)`.
    """
    syms = list(symbols)
    if not syms:
        return []
    quotes = get_market_data().quotes(syms, market=market)
    return [_quote_to_row(q) for q in quotes]


def _article_to_newsitem(a):
    """marketdata.NewsArticle → NewsItem của host (chép thẳng các trường cùng tên).

    lazy import để tránh vòng tham chiếu ở cấp module với news_collector (news_collector
    import md_news của module này ở cấp module).
    """
    from src.platform.marketdata.collectors.news_collector import NewsItem

    return NewsItem(
        source=a.source,
        external_id=a.external_id,
        title=a.title,
        content=a.content,
        publish_time=a.publish_time,
        symbols=a.symbols,
        importance=a.importance,
        url=a.url,
    )


def md_news(
    symbols: list[str], since_hours: int = 2, names: dict[str, str] | None = None
) -> list:
    """Gộp tin tức (tin cổ phiếu riêng lẻ + công bố), trả về list[NewsItem] (cùng hình dạng với NewsCollector.fetch_all cũ).

    Phía host dùng datetime.now() để lọc since được (bên trong gói không được lén gọi
    datetime.now(), bắt buộc bên gọi truyền now tường minh).

    Hàm đồng bộ; bên gọi async dùng `await asyncio.to_thread(md_news, ...)`.
    """
    from datetime import datetime, timezone

    # publish_time của news vendor trong gói có múi giờ (UTC); nên now ở đây cũng bắt buộc phải có múi giờ,
    # nếu không, phép lọc since sẽ báo "can't compare offset-naive and offset-aware datetimes".
    arts = get_market_data().news(
        list(symbols or []), since_hours=since_hours, names=names,
        now=datetime.now(timezone.utc),
    )
    return [_article_to_newsitem(a) for a in arts]


def md_news_by_keyword(keyword: str) -> list:
    """Tìm tin tiếng Trung theo từ khóa (từ ngành/chủ đề), trả về list[NewsItem]. Đồng bộ."""
    arts = get_market_data().news_by_keyword(keyword)
    return [_article_to_newsitem(a) for a in arts]


def md_stock_data(symbols: list[str], market: str) -> list:
    """Trả về list[StockData] (cùng hình dạng với AkshareCollector.get_stock_data cũ). Đồng bộ."""
    from src.platform.marketdata.models import MarketCode, StockData

    syms = list(symbols)
    if not syms:
        return []
    quotes = get_market_data().quotes(syms, market=market)
    return [StockData(
        symbol=q.symbol, name=q.name or "", market=MarketCode(q.market),
        current_price=q.current_price or 0.0, change_pct=q.change_pct or 0.0,
        change_amount=q.change_amount or 0.0, volume=q.volume or 0.0,
        turnover=q.turnover or 0.0, open_price=q.open_price or 0.0,
        high_price=q.high_price or 0.0, low_price=q.low_price or 0.0,
        prev_close=q.prev_close or 0.0) for q in quotes]
