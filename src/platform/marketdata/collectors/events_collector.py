import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

logger = logging.getLogger(__name__)


@dataclass
class EventItem:
    source: str
    external_id: str
    event_type: str
    title: str
    publish_time: datetime
    symbols: list[str]
    importance: int
    url: str


# API lấy toàn văn công bố thông tin của EastMoney (văn bản thuần): art_code -> data.notice_content.
# Đi qua proxy hệ thống (trust_env=True, env HTTP_PROXY); chuỗi chứng chỉ của EastMoney thỉnh thoảng lỗi nên verify=False.
ANN_CONTENT_API_URL = "https://np-cnotice-stock.eastmoney.com/api/content/ann"


def fetch_announcement_fulltext(
    art_code: str,
    *,
    timeout_s: float = 8.0,
    proxy: str | None = None,
) -> str:
    """Lấy toàn văn công bố Đông Tài theo art_code (văn bản thuần).

    Thành công thì trả ``data.notice_content`` đã bỏ khoảng trắng; hỏng kiểu gì (mạng/đọc
    dữ liệu/rỗng) cũng trả chuỗi rỗng — bên gọi dựa vào đó để fail-soft chỉ giữ tiêu đề.

    Args:
        art_code: số hiệu duy nhất của công bố (EventItem.external_id)
        timeout_s: thời gian chờ của yêu cầu
        proxy: proxy tường minh (mặc định không đi qua proxy trong env)
    """
    if not art_code:
        return ""
    params = {
        "art_code": str(art_code),
        "client_source": "web",
        "page_index": 1,
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }
    try:
        timeout = httpx.Timeout(timeout_s, connect=min(timeout_s, 5.0))
        with httpx.Client(
            timeout=timeout,
            verify=False,
            headers=headers,
            follow_redirects=True,
            trust_env=True,  # Đi qua proxy hệ thống (env HTTP_PROXY, do apply_proxy_env đặt thống nhất)
            proxy=proxy,
        ) as client:
            resp = client.get(ANN_CONTENT_API_URL, params=params)
            resp.raise_for_status()
            data = resp.json() or {}
        content = ((data.get("data") or {}).get("notice_content")) or ""
        return str(content).strip()
    except Exception as e:
        logger.debug(f"公告全文获取失败 art_code={art_code}: {type(e).__name__}: {e!r}")
        return ""


def get_market_data():
    """Import lười, tránh phụ thuộc vòng lúc nạp module (cũng tiện monkeypatch khi kiểm thử)."""
    from src.platform.marketdata.marketdata_client import get_market_data as _g
    return _g()


class EastMoneyEventsCollector:
    """A-share event collector based on EastMoney notices.

    Uses the same notices endpoint as news collector, but returns structured event types.
    """

    source = "eastmoney"

    def __init__(
        self,
        *,
        timeout_s: float = 10.0,
        connect_timeout_s: float | None = None,
        verify_ssl: bool = False,
        proxy: str | None = None,
        retries: int = 1,
        backoff_s: float = 0.6,
    ):
        # timeout_s/connect_timeout_s/verify_ssl/proxy/retries/backoff_s chỉ giữ lại để tương thích chữ ký của các phía gọi cũ
        # (cấu hình DataSource, EventsCollector.COLLECTOR_MAP, EastmoneyEventsProvider vẫn dựng thực thể theo các tham số này);
        # việc lấy dữ liệu đã chuyển sang gói marketdata, nên hiện các tham số này không còn được logic bên trong dùng.
        self.last_error: str | None = None

    async def fetch_events(
        self,
        symbols: list[str] | None = None,
        *,
        since: datetime | None = None,
        page_size: int = 50,
    ) -> list[EventItem]:
        import asyncio as _asyncio

        symbols_list = list(symbols or [])
        if not symbols_list:
            return []

        # Ngữ nghĩa since: md.events lọc theo cửa sổ since_days ngày, còn phương thức này lọc theo đúng datetime của since.
        # Suy ngược từ since ra một since_days đủ rộng, lấy dữ liệu về rồi lọc lại chính xác theo since gốc.
        if since is not None:
            delta_days = (datetime.now() - since).days
            since_days = max(1, delta_days + 1)
        else:
            # since=None thì không lọc theo thời gian; dùng cửa sổ đủ lớn để cho kết quả xấp xỉ tương đương
            # (kết quả thực tế vẫn bị giới hạn bởi page_size của API thượng nguồn nên không kéo thêm dữ liệu quá cũ).
            since_days = 3650

        md_items = await _asyncio.to_thread(
            get_market_data().events,
            symbols_list,
            market="CN",
            since_days=since_days,
        )

        result: list[EventItem] = []
        for it in md_items:
            if since and it.publish_time < since:
                continue
            result.append(
                EventItem(
                    source=it.source,
                    external_id=it.external_id,
                    event_type=it.event_type,
                    title=it.title,
                    publish_time=it.publish_time,
                    symbols=it.symbols,
                    importance=it.importance,
                    url=it.url,
                )
            )

        result.sort(key=lambda x: (x.publish_time, x.importance), reverse=True)

        seen: set[tuple[str, str]] = set()
        uniq: list[EventItem] = []
        for ev in result:
            key = (ev.source, ev.external_id)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(ev)

        return uniq


class EventsCollector:
    """Aggregate events collectors."""

    COLLECTOR_MAP = {
        "eastmoney": lambda config: EastMoneyEventsCollector(
            timeout_s=(config or {}).get("timeout_s", 10.0),
            connect_timeout_s=(config or {}).get("connect_timeout_s"),
            verify_ssl=(config or {}).get("verify_ssl", False),
            proxy=(config or {}).get("proxy"),
            retries=(config or {}).get("retries", 1),
            backoff_s=(config or {}).get("backoff_s", 0.6),
        ),
    }

    def __init__(self, collectors: list[EastMoneyEventsCollector] | None = None):
        self.collectors = collectors or [EastMoneyEventsCollector()]

    @classmethod
    def from_database(cls) -> "EventsCollector":
        from src.platform.persistence.database import SessionLocal
        from src.platform.persistence.models import DataSource

        collectors = []
        db = SessionLocal()
        try:
            data_sources = (
                db.query(DataSource)
                .filter(DataSource.type == "events", DataSource.enabled == True)
                .order_by(DataSource.priority)
                .all()
            )
            for ds in data_sources:
                factory = cls.COLLECTOR_MAP.get(ds.provider)
                if not factory:
                    continue
                try:
                    collectors.append(factory(ds.config or {}))
                except Exception:
                    pass
        finally:
            db.close()

        if not collectors:
            collectors = [EastMoneyEventsCollector()]
        return cls(collectors=collectors)

    async def fetch_all(
        self,
        *,
        symbols: list[str] | None = None,
        since_days: int = 7,
    ) -> list[EventItem]:
        import asyncio

        since = datetime.now() - timedelta(days=max(int(since_days), 1))

        async def fetch_one(c) -> list[EventItem]:
            try:
                return await c.fetch_events(symbols=symbols, since=since)
            except Exception as e:
                logger.warning(f"Events collector failed: {e}")
                return []

        results = await asyncio.gather(*[fetch_one(c) for c in self.collectors])
        all_items: list[EventItem] = []
        for items in results:
            all_items.extend(items)

        all_items.sort(key=lambda x: (x.publish_time, x.importance), reverse=True)
        seen: set[tuple[str, str]] = set()
        uniq: list[EventItem] = []
        for it in all_items:
            key = (it.source, it.external_id)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(it)
        return uniq
