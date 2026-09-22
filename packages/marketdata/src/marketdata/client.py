"""Lối vào dạng đối tượng: tiêm ConfigProvider (+ MetricsSink tùy chọn), phơi ra ngoài quotes()/health()."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from marketdata.cache import TTLCache
from marketdata.defaults import InMemoryMetricsSink
from marketdata.engine import Engine
from marketdata.http import record_error
from marketdata.ports import ConfigProvider, MetricsSink
from marketdata.registry import build_vendors
from marketdata.symbol import Symbol
from marketdata.types import (
    CapitalFlow,
    DividendItem,
    DragonTigerItem,
    EventItem,
    FlashNews,
    Fundamentals,
    HotBoard,
    HotStock,
    MarginItem,
    NewsArticle,
    NorthboundItem,
    Quote,
    Request,
    ShareholderItem,
)
from marketdata.vendors.discovery import DiscoveryVendor
from marketdata.vendors.news import EastmoneyStockNewsVendor

# secid của chỉ số (EastMoney): quy tắc tiền tố của chỉ số khác với cổ phiếu riêng lẻ nên phải ánh xạ tường minh, không thì áp quy tắc cổ phiếu sẽ lấy nhầm mã.
# EastMoney không hỗ trợ nến cho chỉ số Mỹ nên không đưa vào danh sách → index_klines trả rỗng, hạ cấp mềm.
INDEX_SECID: dict[str, str] = {
    "000300": "1.000300",   # Chỉ số CSI 300
    "000001": "1.000001",   # Chỉ số Thượng Hải
    "399001": "0.399001",   # Chỉ số Thâm Quyến
    "399006": "0.399006",   # Chỉ số ChiNext
    "HSI": "100.HSI",       # Chỉ số Hang Seng
}

# Mã Tencent gốc của các chỉ số (đường dự phòng qua Tencent của index_klines; chỉ số Mỹ không có secid ở EastMoney nên chỉ còn lối này,
# Tencent chỉ trả vài cây nến gần nhất cho chỉ số Mỹ, ngắn nhưng dùng được).
INDEX_TENCENT: dict[str, str] = {
    "000001": "sh000001",   # Chỉ số Thượng Hải
    "399001": "sz399001",   # Chỉ số Thâm Quyến
    "399006": "sz399006",   # Chỉ số ChiNext
    "000300": "sh000300",   # Chỉ số CSI 300
    "HSI": "hkHSI",         # Chỉ số Hang Seng
    "IXIC": "usIXIC",       # Nasdaq
    "DJI": "usDJI",         # Dow Jones
    "INX": "usINX",         # S&P 500
}


class MarketData:
    def __init__(self, config: ConfigProvider, metrics: MetricsSink | None = None):
        self.config = config
        self.metrics = metrics or InMemoryMetricsSink()
        self._quote_engine = Engine(
            datatype="quote",
            vendors=build_vendors("quote"),
            config=config,
            metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=5.0),
            default_ttl=5.0,
        )
        self._kline_engine = Engine(
            datatype="kline",
            vendors=build_vendors("kline"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=0.0), default_ttl=0.0,
        )
        self._capital_flow_engine = Engine(
            datatype="capital_flow",
            vendors=build_vendors("capital_flow"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=0.0), default_ttl=0.0,
        )
        self._events_engine = Engine(
            datatype="events",
            vendors=build_vendors("events"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=0.0), default_ttl=0.0,
        )
        # flash_news (tin nhanh 7×24) ở cấp thị trường (symbols luôn rỗng), nhưng vẫn đi qua Engine để có chính/phụ, bộ đệm và theo dõi sức khỏe;
        # khác với discovery (không vào Engine) ở chỗ: flash_news có nhiều nguồn cạnh tranh và cần bộ đệm TTL thống nhất.
        self._flash_news_engine = Engine(
            datatype="flash_news",
            vendors=build_vendors("flash_news"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=30.0), default_ttl=30.0,
        )
        # discovery (bảng xếp hạng nổi bật của EastMoney) ở cấp thị trường, một nguồn, không theo mô hình symbol, nên không vào Engine / không vào phân loại
        # DataSource — md ủy quyền thẳng cho DiscoveryVendor.
        self._discovery = DiscoveryVendor()
        # news (tin tức) mang ngữ nghĩa tổng hợp (hỏi song song mọi nguồn đang bật rồi gộp và khử trùng lặp), không phải ngữ nghĩa hạ cấp khi lỗi
        # (tìm được một nguồn là dừng); ép nó vào mô hình chính/phụ của Engine là lệch thiết kế nên không đưa vào Engine — chỉ mượn build_vendors của registry
        # để dùng lại thực thể vendor, còn phần gộp / khử trùng lặp / sắp xếp / lọc since thì news() tự làm.
        self._news_vendors = build_vendors("news")
        self._fundamentals_engine = Engine(
            datatype="fundamentals",
            vendors=build_vendors("fundamentals"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=300.0), default_ttl=300.0,
        )
        # Bảng giao dịch đột biến / giao dịch ký quỹ / số lượng cổ đông / cổ tức: thuộc mặt thị trường và dòng tiền, đều đi qua cùng một dạng endpoint datacenter của EastMoney,
        # tần suất cập nhật thấp (theo ngày / theo kỳ) nên dùng luôn TTL 300s giống fundamentals.
        self._dragon_tiger_engine = Engine(
            datatype="dragon_tiger",
            vendors=build_vendors("dragon_tiger"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=300.0), default_ttl=300.0,
        )
        self._margin_engine = Engine(
            datatype="margin",
            vendors=build_vendors("margin"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=300.0), default_ttl=300.0,
        )
        self._shareholders_engine = Engine(
            datatype="shareholders",
            vendors=build_vendors("shareholders"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=300.0), default_ttl=300.0,
        )
        self._dividend_engine = Engine(
            datatype="dividend",
            vendors=build_vendors("dividend"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=300.0), default_ttl=300.0,
        )
        # Dòng vốn phía Bắc (mua ròng lũy kế theo phút trong ngày, nguồn hexin của Tonghuashun): cấp thị trường, một nguồn, cập nhật theo phút
        # nhưng giá trị lũy kế trong ngày biến động chậm, nên dùng TTL 60s giống flash_news (bám sát tính chất "tăng dần trong phiên" hơn 300s).
        self._northbound_engine = Engine(
            datatype="northbound",
            vendors=build_vendors("northbound"),
            config=config, metrics=self.metrics,
            cache=TTLCache(default_ttl_sec=60.0), default_ttl=60.0,
        )

    def klines(self, symbol: str, *, market: str, days: int = 120, min_count: int = 1) -> list:
        """Lấy nến ngày theo chính-phụ dựa trên priority (thiếu thì thử nguồn kế, thiếu hết thì lấy nguồn dài nhất). Trả về list[Bar].
        Không đệm trong gói (cache_ttl_sec=0); host tự lo đệm."""
        req = Request(symbols=(symbol,), market=market, timeframe="day", limit=days,
                      extra=(("days", days),))
        resp = self._kline_engine.fetch(req, min_count=min_count, cache_ttl_sec=0)
        return resp.data or []

    def quotes(self, symbols: list[str | Symbol], *, market: str | None = None) -> list[Quote]:
        """Báo giá hàng loạt. symbols nằm ở nhiều thị trường được: không ghi market tường minh thì tự nhận diện theo mã rồi gom nhóm."""
        groups: dict[str, list[Symbol]] = {}
        for raw in symbols:
            sym = raw if isinstance(raw, Symbol) else Symbol.parse(raw, market)
            groups.setdefault(sym.market.value, []).append(sym)

        out: list[Quote] = []
        for mkt, syms in groups.items():
            req = Request(symbols=tuple(s.code for s in syms), market=mkt)
            resp = self._quote_engine.fetch(req)
            if resp.ok and resp.data:
                out.extend(resp.data)
        return out

    def index_quotes(self, tencent_symbols: list[str]) -> list[dict]:
        """Lấy bảng giá theo ký hiệu chỉ số gốc của Tencent (sh000001/hkHSI/usDJI…), không qua Symbol.parse.

        Mã chỉ số có thể trùng số với mã cổ phiếu (như 000001 vừa là Ping An Bank vừa là
        chỉ số Thượng Hải), nên đi đường ký hiệu tường minh.
        Trả về list[dict].
        """
        from marketdata.vendors.tencent import fetch_raw
        return fetch_raw(list(tencent_symbols)) if tencent_symbols else []

    def index_klines(self, code: str, *, market: str, days: int = 120) -> list:
        """Nến ngày của chỉ số: secid Đông Tài là nguồn chính; hỏng/chưa ánh xạ (như chỉ số Mỹ) thì lùi về ký hiệu gốc của Tencent; không có gì cả → [].

        Phần hứng bằng Tencent lấp hai chỗ hụt: ① khi push2his của Đông Tài bị proxy/kiểm
        soát rủi ro chặn thì chỉ số CN/HK vẫn có dữ liệu; ② chỉ số Mỹ (IXIC/DJI/INX) Đông
        Tài không có secid, Tencent thì ra được (chỉ vài cây gần nhất, ngắn nhưng dùng được).
        Trả về list[Bar].
        """
        c = str(code).strip()
        secid = INDEX_SECID.get(c) or INDEX_SECID.get(c.upper())
        if secid:
            from marketdata.vendors.kline import fetch_eastmoney_kline
            bars = fetch_eastmoney_kline(secid, days)
            if bars:
                return bars
        tsym = INDEX_TENCENT.get(c) or INDEX_TENCENT.get(c.upper())
        if tsym:
            from marketdata.vendors.kline import fetch_tencent_kline_raw
            return fetch_tencent_kline_raw(tsym, days)
        return []

    def capital_flow(self, symbol: str, *, market: str = "CN") -> CapitalFlow | None:
        """Dòng tiền của một mã. Không đệm trong gói (cache_ttl_sec=0); host tự lo đệm."""
        req = Request(symbols=(symbol,), market=market)
        resp = self._capital_flow_engine.fetch(req, cache_ttl_sec=0)
        data = resp.data or []
        return data[0] if data else None

    def events(self, symbols: list[str], *, market: str = "CN", since_days: int = 7) -> list[EventItem]:
        """Sự kiện có cấu trúc (công bố Đông Tài). Nhiều symbols cùng lúc. Không đệm trong gói (cache_ttl_sec=0); host tự lo đệm."""
        req = Request(symbols=tuple(symbols), market=market, since_hours=since_days * 24,
                      extra=(("since_days", since_days),))
        resp = self._events_engine.fetch(req, cache_ttl_sec=0)
        return resp.data or []

    def flash_news(self, *, market: str = "CN", limit: int = 50, keyword: str | None = None) -> list[FlashNews]:
        """Tin nhanh (7×24). Cấp thị trường, symbols luôn rỗng. Không đệm thêm một lớp trong gói — dùng TTL 30s mặc định của Engine."""
        req = Request(symbols=(), market=market, limit=limit)
        resp = self._flash_news_engine.fetch(req)
        data = resp.data or []
        if keyword:
            data = [x for x in data if keyword in (x.title or "") or keyword in (x.content or "")]
        return data

    def news(
        self,
        symbols: list[str],
        *,
        market: str = "CN",
        since_hours: int = 2,
        names: dict[str, str] | None = None,
        now: datetime | None = None,
    ) -> list[NewsArticle]:
        """Tin tức (tin cổ phiếu riêng lẻ + công bố) — ngữ nghĩa gộp, không phải chuyển khi hỏng:
        tra mọi nguồn đang bật rồi gộp kết quả và gộp trùng, chứ không phải "tìm được một
        cái là dừng" (khác ngữ nghĩa chính-phụ của quotes()/klines()), nên không đi qua Engine.

        Khớp với ngữ nghĩa gộp của NewsCollector.fetch_all bên PanWatch:
        - Nguồn công bố (vendor="eastmoney") dùng cửa sổ rộng hơn max(since_hours, 72) (công
          bố phát hành thưa, cửa sổ hẹp quá thì dễ không vớt được bản nào); các nguồn khác
          dùng since_hours. Giá trị cửa sổ được chuyển thẳng vào config của vendor (hiện cả
          3 vendor đều chưa đọc — phần lọc since thật làm ở chính phương thức này, bên trong
          vendor không được gọi datetime.now() không tham số).
        - Gộp xong thì gộp trùng theo external_id, giữ bản xuất hiện trước (tức nguồn có ưu tiên cao hơn được giữ).
        - Xếp theo publish_time giảm dần.
        - Lọc since cần mốc "lúc này": truyền now thì mới lọc (mỗi bản chọn cửa sổ theo nguồn
          của nó, quy tắc như trên); không truyền now thì không lọc, trả nguyên toàn bộ kết
          quả đã gộp (bên trong gói tuyệt đối không lén gọi datetime.now()).
        """
        syms = [Symbol.parse(s, market) for s in symbols]
        srcs = sorted(self.config.sources_for("news", market), key=lambda s: s.priority)

        all_articles: list[NewsArticle] = []
        for src in srcs:
            if not src.enabled:
                continue
            vendor = self._news_vendors.get(src.vendor)
            if vendor is None:
                continue
            if vendor.supports_markets and market not in vendor.supports_markets:
                continue

            window = max(since_hours, 72) if src.vendor == "eastmoney" else since_hours
            call_config = {**(src.config or {}), "symbol_names": names or {}, "since_hours": window}

            t0 = time.monotonic()
            try:
                articles = vendor.fetch(syms, call_config) or []
            except Exception as e:
                latency = int((time.monotonic() - t0) * 1000)
                self.metrics.record(vendor=src.vendor, datatype="news", market=market,
                                    ok=False, count=0, latency_ms=latency, error=str(e))
                record_error(f"{src.vendor}: {type(e).__name__}: {e}")
                continue

            latency = int((time.monotonic() - t0) * 1000)
            if articles:
                self.metrics.record(vendor=src.vendor, datatype="news", market=market,
                                    ok=True, count=len(articles), latency_ms=latency)
            else:
                self.metrics.record(vendor=src.vendor, datatype="news", market=market,
                                    ok=False, count=0, latency_ms=latency, error="empty")
            all_articles.extend(articles)

        seen: set[str] = set()
        deduped: list[NewsArticle] = []
        for a in all_articles:
            if a.external_id in seen:
                continue
            seen.add(a.external_id)
            deduped.append(a)

        deduped.sort(key=lambda a: a.publish_time, reverse=True)

        if now is not None:
            def _keep(a: NewsArticle) -> bool:
                w = max(since_hours, 72) if a.source == "eastmoney" else since_hours
                return a.publish_time >= now - timedelta(hours=w)
            deduped = [a for a in deduped if _keep(a)]

        return deduped

    def news_by_keyword(self, keyword: str, *, market: str = "CN") -> list[NewsArticle]:
        """按任意关键词(行业/主题词,如"新能源汽车")搜中文新闻,不限股票代码。
        直接复用东财搜索 vendor 的 fetch_by_keyword,单一源、不经聚合/去重。
        market 目前未使用(该 vendor 只支持中文搜索),保留参数位供未来扩展。
        """
        return EastmoneyStockNewsVendor.fetch_by_keyword(keyword)

    def fundamentals(self, symbols: list[str | Symbol], *, market: str | None = None) -> list[Fundamentals]:
        """批量基本面/财务(按 symbol)。symbols 可跨市场:未显式给 market 时按代码自动识别并分组。
        照 quotes() 范式:按市场分组、每组建 Request、逐组 engine.fetch、合并结果。"""
        groups: dict[str, list[Symbol]] = {}
        for raw in symbols:
            sym = raw if isinstance(raw, Symbol) else Symbol.parse(raw, market)
            groups.setdefault(sym.market.value, []).append(sym)

        out: list[Fundamentals] = []
        for mkt, syms in groups.items():
            req = Request(symbols=tuple(s.code for s in syms), market=mkt)
            resp = self._fundamentals_engine.fetch(req)
            if resp.ok and resp.data:
                out.extend(resp.data)
        return out

    def dragon_tiger(self, *, date: str | None = None, market: str = "CN") -> list[DragonTigerItem]:
        """龙虎榜(市场级,单日快照)。date 未给出时不猜测"今天",直接返回 []。"""
        req = Request(symbols=(), market=market, extra=(("date", date),))
        resp = self._dragon_tiger_engine.fetch(req)
        return resp.data or []

    def margin(self, symbols: list[str | Symbol], *, market: str | None = None) -> list[MarginItem]:
        """批量融资融券(按 symbol,取每只最新一条快照)。照 fundamentals() 分组范式。"""
        groups: dict[str, list[Symbol]] = {}
        for raw in symbols:
            sym = raw if isinstance(raw, Symbol) else Symbol.parse(raw, market)
            groups.setdefault(sym.market.value, []).append(sym)

        out: list[MarginItem] = []
        for mkt, syms in groups.items():
            req = Request(symbols=tuple(s.code for s in syms), market=mkt)
            resp = self._margin_engine.fetch(req)
            if resp.ok and resp.data:
                out.extend(resp.data)
        return out

    def shareholders(self, symbols: list[str | Symbol], *, market: str | None = None) -> list[ShareholderItem]:
        """批量股东户数(按 symbol,取每只最新一期)。照 fundamentals() 分组范式。"""
        groups: dict[str, list[Symbol]] = {}
        for raw in symbols:
            sym = raw if isinstance(raw, Symbol) else Symbol.parse(raw, market)
            groups.setdefault(sym.market.value, []).append(sym)

        out: list[ShareholderItem] = []
        for mkt, syms in groups.items():
            req = Request(symbols=tuple(s.code for s in syms), market=mkt)
            resp = self._shareholders_engine.fetch(req)
            if resp.ok and resp.data:
                out.extend(resp.data)
        return out

    def dividend(self, symbols: list[str | Symbol], *, market: str | None = None) -> list[DividendItem]:
        """批量分红(按 symbol,返回每只全部历史)。照 fundamentals() 分组范式。"""
        groups: dict[str, list[Symbol]] = {}
        for raw in symbols:
            sym = raw if isinstance(raw, Symbol) else Symbol.parse(raw, market)
            groups.setdefault(sym.market.value, []).append(sym)

        out: list[DividendItem] = []
        for mkt, syms in groups.items():
            req = Request(symbols=tuple(s.code for s in syms), market=mkt)
            resp = self._dividend_engine.fetch(req)
            if resp.ok and resp.data:
                out.extend(resp.data)
        return out

    def northbound(self, *, market: str = "CN") -> list[NorthboundItem]:
        """北向资金(市场级,symbols 恒空)。照 flash_news() 无 symbols 范式。"""
        req = Request(symbols=(), market=market)
        resp = self._northbound_engine.fetch(req)
        return resp.data or []

    def health(self) -> dict[str, dict]:
        """每个 vendor 的内存健康度快照(成功率 / p50 延迟 / 最近错误)。"""
        return self.metrics.snapshot()

    def hot_stocks(self, **kw) -> list[HotStock]:
        """热门/异动股(东财榜单,市场级、不经 Engine)。"""
        return self._discovery.hot_stocks(**kw)

    def hot_boards(self, **kw) -> list[HotBoard]:
        """热门板块(东财榜单,市场级、不经 Engine)。"""
        return self._discovery.hot_boards(**kw)

    def board_stocks(self, **kw) -> list[HotStock]:
        """板块成分股榜单(东财,市场级、不经 Engine)。"""
        return self._discovery.board_stocks(**kw)
