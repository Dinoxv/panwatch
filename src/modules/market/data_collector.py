"""Bộ quản lý nguồn dữ liệu thống nhất"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from marketdata import PACKAGE_VENDORS_BY_TYPE, capture_errors

from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import DataSource
from src.platform.marketdata.models import MarketCode

# Bộ mẫu thống nhất để kiểm thử nguồn dữ liệu. Mỗi thị trường cố định hai mã ổn định, dễ nhận ra, tránh việc tạo nguồn mới
# mà chỉ kiểm thử được cổ phiếu A, khiến lỗi định tuyến thị trường của provider Hồng Kông / Mỹ tới tận môi trường thật mới lộ.
DEFAULT_TEST_SYMBOLS_BY_MARKET: dict[str, tuple[str, str]] = {
    "CN": ("600519", "601127"),
    "HK": ("00700", "00386"),
    "US": ("AAPL", "NVDA"),
}
DEFAULT_TEST_SYMBOLS: tuple[str, ...] = tuple(
    symbol
    for symbols in DEFAULT_TEST_SYMBOLS_BY_MARKET.values()
    for symbol in symbols
)

logger = logging.getLogger(__name__)

# Số test_symbols tối đa mà một lần "kiểm thử" nguồn dữ liệu sẽ chạy (đặt trần, phòng người dùng dán một danh sách dài làm quá tải nguồn).
# Lấy 10 để phủ các cấu hình thường gặp (trước đây kline/capital_flow gán cứng [:3], quote/events [:5], làm mã thứ 4 / thứ 6
# mà người dùng cấu hình bị cắt âm thầm, gây tình huống bất ngờ "cấu hình N mã mà chỉ trả về vài mã đầu").
_TEST_SYMBOL_LIMIT = 10


@dataclass
class CollectorResult:
    """Kết quả thu thập"""

    success: bool
    data: Any = None
    count: int = 0
    duration_ms: int = 0
    error: str = ""
    source_name: str = ""
    source_provider: str = ""
    # Các mã thực sự được chạy lần này (kể cả mã không trả về dữ liệu), dùng để hộp thoại kiểm thử hiển thị lại cấu hình / giá trị mặc định.
    test_symbols: list[str] = field(default_factory=list)
    # Khi chỉ thành công một phần thì giữ lý do lỗi của từng mã, tránh để mã không có dữ liệu (ví dụ APPL) biến mất lặng lẽ.
    errors: list[dict[str, str]] = field(default_factory=list)


@dataclass
class CollectorLog:
    """Nhật ký thu thập"""

    timestamp: datetime
    source_name: str
    source_type: str
    action: str  # "start" / "success" / "error"
    message: str
    duration_ms: int = 0
    count: int = 0


class DataCollectorManager:
    """
    Bộ quản lý nguồn dữ liệu thống nhất

    Cung cấp giao diện thu thập dữ liệu thống nhất, hỗ trợ:
    - Nạp nguồn dữ liệu từ cấu hình trong cơ sở dữ liệu
    - Ghi nhật ký thu thập
    - Thu thập hàng loạt/từng mục
    """

    # Loại nguồn dữ liệu -> (provider -> hàm nhà máy tạo bộ thu thập)
    COLLECTOR_FACTORIES: dict[str, dict[str, Callable]] = {}

    def __init__(self):
        self.logs: list[CollectorLog] = []
        self._register_collectors()

    def _register_collectors(self):
        """Đăng ký mọi bộ thu thập"""
        from src.platform.marketdata.collectors.kline_collector import KlineCollector
        from src.platform.marketdata.collectors.capital_flow_collector import CapitalFlowCollector
        from src.platform.marketdata.collectors.events_collector import EastMoneyEventsCollector

        self.COLLECTOR_FACTORIES = {
            "kline": {
                "tencent": lambda cfg: ("tencent", KlineCollector),
            },
            "capital_flow": {
                "eastmoney": lambda cfg: CapitalFlowCollector(MarketCode.CN),
            },
            "chart": {
                "xueqiu": lambda cfg: ("xueqiu", cfg),
                "eastmoney": lambda cfg: ("eastmoney", cfg),
            },
            "events": {
                "eastmoney": lambda cfg: EastMoneyEventsCollector(),
            },
        }

    def _log(
        self,
        source_name: str,
        source_type: str,
        action: str,
        message: str,
        duration_ms: int = 0,
        count: int = 0,
    ):
        """Ghi nhật ký"""
        log = CollectorLog(
            timestamp=datetime.now(),
            source_name=source_name,
            source_type=source_type,
            action=action,
            message=message,
            duration_ms=duration_ms,
            count=count,
        )
        self.logs.append(log)

        # Đồng thời ghi ra logger: error đi mức WARNING; start/success chỉ là nhịp tim tầng dưới nên hạ xuống DEBUG.
        # Bảng nhật ký trên giao diện luôn đọc bản ghi đầy đủ từ self.logs, không bị chỗ này ảnh hưởng.
        if action == "error":
            logger.warning(f"[{source_name}] {message}")
        else:
            logger.debug(f"[{source_name}] {message}")

    def get_logs(self) -> list[dict]:
        """Lấy nhật ký (để hiển thị trên giao diện)"""
        return [
            {
                "timestamp": log.timestamp.strftime("%H:%M:%S"),
                "source_name": log.source_name,
                "source_type": log.source_type,
                "action": log.action,
                "message": log.message,
                "duration_ms": log.duration_ms,
                "count": log.count,
            }
            for log in self.logs
        ]

    def clear_logs(self):
        """Xóa sạch nhật ký"""
        self.logs = []

    def get_enabled_sources(self, source_type: str) -> list[DataSource]:
        """Lấy các nguồn dữ liệu đang bật thuộc loại chỉ định"""
        db = SessionLocal()
        try:
            return (
                db.query(DataSource)
                .filter(DataSource.type == source_type, DataSource.enabled == True)
                .order_by(DataSource.priority)
                .all()
            )
        finally:
            db.close()

    def get_source_by_id(self, source_id: int) -> DataSource | None:
        """Lấy nguồn dữ liệu theo ID"""
        db = SessionLocal()
        try:
            return db.query(DataSource).filter(DataSource.id == source_id).first()
        finally:
            db.close()

    def _get_stock_names(self, symbols: list[str]) -> dict[str, str]:
        """Lấy ánh xạ từ mã cổ phiếu sang tên"""
        from src.platform.persistence.models import Stock

        # Ánh xạ tên cho các mã kiểm thử mặc định
        default_names = {
            "601127": "赛力斯",
            "600519": "贵州茅台",
            "000001": "平安银行",
            "000858": "五粮液",
            "300750": "宁德时代",
        }

        db = SessionLocal()
        try:
            stocks = db.query(Stock).filter(Stock.symbol.in_(symbols)).all()
            result = {s.symbol: s.name for s in stocks}

            # Mã chưa có trong cơ sở dữ liệu thì dùng tên mặc định
            for symbol in symbols:
                if symbol not in result and symbol in default_names:
                    result[symbol] = default_names[symbol]

            return result
        except Exception as e:
            logger.warning(f"获取股票名称失败: {e}")
            # Trả về tên mặc định
            return {s: default_names.get(s, s) for s in symbols if s in default_names}
        finally:
            db.close()

    async def collect_news(
        self, symbols: list[str], hours: int = 12
    ) -> CollectorResult:
        """Thu thập tin tức (dùng mọi nguồn dữ liệu tin tức đang bật)"""
        from src.platform.marketdata.collectors.news_collector import NewsCollector

        start_time = datetime.now()
        self._log("新闻采集", "news", "start", f"开始采集 {len(symbols)} 只股票的新闻")

        try:
            collector = NewsCollector.from_database()
            news_list = await collector.fetch_all(symbols=symbols, since_hours=hours)

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log(
                "新闻采集",
                "news",
                "success",
                f"采集完成，共 {len(news_list)} 条",
                duration_ms=duration_ms,
                count=len(news_list),
            )

            return CollectorResult(
                success=True,
                data=news_list,
                count=len(news_list),
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log("新闻采集", "news", "error", str(e), duration_ms=duration_ms)
            return CollectorResult(success=False, error=str(e), duration_ms=duration_ms)

    async def collect_kline(
        self, symbol: str, market: str = "CN", days: int = 60
    ) -> CollectorResult:
        """Thu thập dữ liệu nến"""
        from src.platform.marketdata.collectors.kline_collector import KlineCollector
        from src.platform.marketdata.models import MarketCode

        start_time = datetime.now()
        self._log("K线数据", "kline", "start", f"获取 {symbol} 的 K 线数据")

        try:
            market_code = MarketCode(market)
            collector = KlineCollector(market_code)
            summary = collector.get_kline_summary(symbol)

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            if summary.get("error"):
                self._log(
                    "K线数据",
                    "kline",
                    "error",
                    summary["error"],
                    duration_ms=duration_ms,
                )
                return CollectorResult(
                    success=False, error=summary["error"], duration_ms=duration_ms
                )

            self._log(
                "K线数据",
                "kline",
                "success",
                f"获取成功，最新收盘价 {summary.get('last_close', 'N/A')}",
                duration_ms=duration_ms,
            )

            return CollectorResult(
                success=True,
                data=summary,
                count=1,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log("K线数据", "kline", "error", str(e), duration_ms=duration_ms)
            return CollectorResult(success=False, error=str(e), duration_ms=duration_ms)

    async def collect_capital_flow(self, symbol: str) -> CollectorResult:
        """Thu thập dòng tiền"""
        from src.platform.marketdata.collectors.capital_flow_collector import CapitalFlowCollector

        start_time = datetime.now()
        self._log("资金流向", "capital_flow", "start", f"获取 {symbol} 的资金流向")

        try:
            collector = CapitalFlowCollector(MarketCode.CN)
            data = collector.get_capital_flow(symbol)

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            if not data:
                self._log(
                    "资金流向",
                    "capital_flow",
                    "error",
                    "无数据",
                    duration_ms=duration_ms,
                )
                return CollectorResult(
                    success=False, error="无数据", duration_ms=duration_ms
                )

            self._log(
                "资金流向",
                "capital_flow",
                "success",
                f"获取成功，主力净流入 {data.main_net_inflow / 10000:.2f}万",
                duration_ms=duration_ms,
            )

            return CollectorResult(
                success=True,
                data=data,
                count=1,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log(
                "资金流向", "capital_flow", "error", str(e), duration_ms=duration_ms
            )
            return CollectorResult(success=False, error=str(e), duration_ms=duration_ms)

    async def collect_quote(self, symbols: list[str]) -> CollectorResult:
        """Thu thập bảng giá thời gian thực"""
        from src.platform.marketdata.marketdata_client import md_stock_data

        start_time = datetime.now()
        self._log("实时行情", "quote", "start", f"获取 {len(symbols)} 只股票的行情")

        try:
            stocks = await asyncio.to_thread(md_stock_data, symbols, MarketCode.CN.value)

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log(
                "实时行情",
                "quote",
                "success",
                f"获取成功，共 {len(stocks)} 只",
                duration_ms=duration_ms,
                count=len(stocks),
            )

            return CollectorResult(
                success=True,
                data=stocks,
                count=len(stocks),
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log("实时行情", "quote", "error", str(e), duration_ms=duration_ms)
            return CollectorResult(success=False, error=str(e), duration_ms=duration_ms)

    async def test_source(self, source: DataSource) -> CollectorResult:
        """Kiểm tra một nguồn dữ liệu"""
        test_symbols = source.test_symbols or list(DEFAULT_TEST_SYMBOLS)

        start_time = datetime.now()
        self._log(
            source.name,
            source.type,
            "start",
            f"开始测试，测试股票: {','.join(test_symbols)}",
        )

        try:
            # Thu lý do lỗi thật của vendor / market_get rồi đưa lên giao diện khi thất bại (thay vì chỉ báo chung chung "không có dữ liệu")
            with capture_errors() as errs:
                result = await self._test_source_impl(source, test_symbols)
            result.test_symbols = list(test_symbols)
            if not result.success and errs:
                # Khử trùng lặp giữ thứ tự + cắt bớt rồi ghép thành nguyên nhân thật; nếu sẵn có lỗi cụ thể hơn (ví dụ "provider không có vendor tương ứng") thì giữ nó ở đầu
                seen: dict[str, None] = {}
                for m in errs:
                    seen.setdefault(m, None)
                detail = "; ".join(list(seen)[:8])
                generic = {"", "无数据", "获取行情失败", "获取 K 线数据失败", "获取资金流向失败",
                           "未获取到新闻数据", "未获取到快讯数据", "未获取到基本面数据",
                           "未获取到龙虎榜数据", "未获取到融资融券数据", "未获取到股东数据",
                           "未获取到分红数据", "未获取到北向资金数据"}
                result.error = detail if (result.error or "") in generic else f"{result.error};真因: {detail}"
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            if result.success:
                self._log(
                    source.name,
                    source.type,
                    "success",
                    f"测试成功，获取到 {result.count} 条数据",
                    duration_ms=duration_ms,
                    count=result.count,
                )
            else:
                self._log(
                    source.name,
                    source.type,
                    "error",
                    result.error,
                    duration_ms=duration_ms,
                )

            result.duration_ms = duration_ms
            result.source_name = source.name
            result.source_provider = source.provider
            return result

        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log(
                source.name, source.type, "error", str(e), duration_ms=duration_ms
            )
            return CollectorResult(
                success=False,
                error=str(e),
                duration_ms=duration_ms,
                source_name=source.name,
                source_provider=source.provider,
                test_symbols=list(test_symbols),
            )

    async def _test_source_impl(
        self, source: DataSource, test_symbols: list[str]
    ) -> CollectorResult:
        """Phần cài đặt cụ thể của việc kiểm tra nguồn dữ liệu"""
        if source.type == "news":
            return await self._test_news_source(source, test_symbols)

        elif source.type == "kline":
            # Định tuyến tới đúng Provider theo provider, thay vì gán cứng đi qua tencent (KlineCollector).
            # Token và các cấu hình khác của Tushare / YFinance được tiêm từ source.config.
            return await self._test_kline_source(source, test_symbols)

        elif source.type == "capital_flow":
            from src.platform.marketdata.collectors.capital_flow_collector import CapitalFlowCollector

            collector = CapitalFlowCollector(MarketCode.CN)
            results = []
            for symbol in test_symbols[:_TEST_SYMBOL_LIMIT]:
                data = collector.get_capital_flow(symbol)
                if data:
                    results.append(
                        {
                            "symbol": symbol,
                            "name": data.name,
                            "main_net": data.main_net_inflow,
                            "main_pct": data.main_net_inflow_pct,
                        }
                    )

            return CollectorResult(
                success=len(results) > 0,
                data=results,
                count=len(results),
                error="" if results else "获取资金流向失败",
            )

        elif source.type == "quote":
            # Định tuyến tới đúng Provider theo provider, nhờ vậy Tushare (tạm chưa có quote) / YFinance đều kiểm thử đúng.
            return await self._test_quote_source(source, test_symbols)

        elif source.type == "chart":
            from src.platform.marketdata.collectors.screenshot_collector import ScreenshotCollector
            import base64

            collector = ScreenshotCollector(config={"extra_wait_ms": 3000})
            try:
                symbol = test_symbols[0] if test_symbols else "601127"
                screenshot = await collector.capture(
                    symbol=symbol,
                    name="测试",
                    market="CN",
                    provider=source.provider,
                )
                if screenshot and screenshot.exists:
                    with open(screenshot.filepath, "rb") as f:
                        img_base64 = base64.b64encode(f.read()).decode("utf-8")
                    return CollectorResult(
                        success=True,
                        data={"image": f"data:image/png;base64,{img_base64}"},
                        count=1,
                    )
                return CollectorResult(success=False, error="截图失败")
            finally:
                await collector.close()

        elif source.type == "events":
            from src.platform.marketdata.collectors.events_collector import EastMoneyEventsCollector

            from datetime import timedelta

            # Use a longer window for tests to avoid "recently empty" false negatives.
            # This is only for connectivity/format validation, not for production logic.
            lookback_days = 365
            since = datetime.now() - timedelta(days=lookback_days)
            if source.provider == "eastmoney":
                cfg = source.config or {}
                collector = EastMoneyEventsCollector(
                    timeout_s=cfg.get("timeout_s", 10.0),
                    connect_timeout_s=cfg.get("connect_timeout_s"),
                    verify_ssl=cfg.get("verify_ssl", False),
                    proxy=cfg.get("proxy"),
                    retries=cfg.get("retries", 1),
                    backoff_s=cfg.get("backoff_s", 0.6),
                )
                items = await collector.fetch_events(
                    symbols=test_symbols[:_TEST_SYMBOL_LIMIT],
                    since=since,
                    page_size=100,
                )
                if not items and getattr(collector, "last_error", None):
                    return CollectorResult(
                        success=False,
                        data=[],
                        count=0,
                        error=str(collector.last_error),
                    )
                return CollectorResult(
                    success=len(items) > 0,
                    data=[
                        {
                            "title": i.title[:80],
                            "time": i.publish_time.strftime("%m-%d %H:%M"),
                            "event_type": i.event_type,
                        }
                        for i in items[:10]
                    ],
                    count=len(items),
                    error=""
                    if items
                    else f"未获取到事件数据（lookback={lookback_days}d）",
                )

        elif source.type == "flash_news":
            return await self._test_flash_news_source(source)

        elif source.type == "fundamentals":
            return await self._test_fundamentals_source(source)

        elif source.type == "dragon_tiger":
            return await self._test_dragon_tiger_source(source)

        elif source.type == "margin":
            return await self._test_margin_source(source)

        elif source.type == "shareholders":
            return await self._test_shareholders_source(source)

        elif source.type == "dividend":
            return await self._test_dividend_source(source)

        elif source.type == "northbound":
            return await self._test_northbound_source(source)

        return CollectorResult(
            success=False, error=f"不支持的数据源类型: {source.type}"
        )

    # Các Engine kline/quote/flash_news/fundamentals trong gói chỉ đăng ký đúng những vendor này (nguồn có thẩm quyền xem marketdata.PACKAGE_VENDORS_BY_TYPE).
    # provider không nằm trong tập này = gói chưa cài đặt nguồn đó, phép kiểm thử phải báo lỗi rõ ràng, không được dựng Engine chạy bừa.
    _NEWS_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["news"]
    _KLINE_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["kline"]
    _QUOTE_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["quote"]
    _FLASH_NEWS_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["flash_news"]
    _FUNDAMENTALS_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["fundamentals"]
    _DRAGON_TIGER_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["dragon_tiger"]
    _MARGIN_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["margin"]
    _SHAREHOLDERS_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["shareholders"]
    _DIVIDEND_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["dividend"]
    _NORTHBOUND_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["northbound"]

    async def _test_kline_source(
        self, source: DataSource, test_symbols: list[str]
    ) -> CollectorResult:
        """Kiểm nguồn nến theo provider: đi qua Engine một nguồn của gói marketdata (chỉ vendor đó, không xâu chuỗi dự phòng).

        Kiểm tra là để biết "chính provider này chạy có ổn không", chứ không phải "cả chuỗi
        chính-phụ có fallback nên vẫn chạy được", nên dùng StaticConfigProvider chỉ chứa
        đúng vendor này để cô lập nguồn cần kiểm.
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider, Symbol

        if source.provider not in self._KLINE_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 无对应 vendor，包内未实现该 K 线源",
            )

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"kline": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        results = []
        errors: list[dict[str, str]] = []
        first_error = ""
        for symbol in test_symbols[:_TEST_SYMBOL_LIMIT]:
            market = Symbol.parse(symbol).market.value
            try:
                bars = md.klines(symbol, market=market, days=30)
                if bars:
                    last = bars[-1]
                    results.append(
                        {
                            "symbol": symbol,
                            "last_close": last.close,
                            "last_date": last.date,
                            "count": len(bars),
                        }
                    )
                elif not first_error:
                    first_error = "无数据"
                if not bars:
                    errors.append({"symbol": symbol, "market": market, "error": "无数据"})
            except Exception as e:
                errors.append({"symbol": symbol, "market": market, "error": str(e)})
                if not first_error:
                    first_error = str(e)

        return CollectorResult(
            success=len(results) > 0,
            data=results,
            count=len(results),
            error="" if results else (first_error or "获取 K 线数据失败"),
            errors=errors,
        )

    async def _test_quote_source(
        self, source: DataSource, test_symbols: list[str]
    ) -> CollectorResult:
        """Kiểm nguồn bảng giá theo provider: đi qua Engine một nguồn của gói marketdata (chỉ vendor đó, không xâu chuỗi dự phòng)."""
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        from src.platform.marketdata.marketdata_client import _quote_to_row

        if source.provider not in self._QUOTE_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 无对应 vendor，包内未实现该行情源",
            )

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"quote": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        try:
            quotes = md.quotes(list(test_symbols[:_TEST_SYMBOL_LIMIT]))
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        rows = [_quote_to_row(q) for q in quotes]
        return CollectorResult(
            success=len(rows) > 0,
            data=[
                {
                    "symbol": row["symbol"],
                    "name": row["name"],
                    "price": row["current_price"],
                    "change_pct": row["change_pct"],
                }
                for row in rows
            ],
            count=len(rows),
            error="" if rows else "获取行情失败",
        )

    async def _test_news_source(
        self, source: DataSource, test_symbols: list[str]
    ) -> CollectorResult:
        """Kiểm nguồn tin tức theo provider: đi qua Engine một nguồn của gói marketdata (chỉ vendor đó, không gộp nguồn khác).

        Tin tức là dữ liệu theo symbol; eastmoney_news tìm bằng tên cổ phiếu (hiệu quả hơn
        hẳn tìm bằng mã), nên ở đây lấy luôn ánh xạ tên của mã kiểm thử để truyền vào.
        capture_errors đã bọc bên ngoài test_source, hỏng thì tự phơi nguyên nhân thật
        (gồm cả việc Xueqiu bị WAF chặn).
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._NEWS_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 无对应 vendor，包内未实现该新闻源",
            )

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"news": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        names = self._get_stock_names(test_symbols)

        try:
            # publish_time của news trong gói có múi giờ (UTC) nên now cũng phải có múi giờ, nếu không phép lọc since sẽ hỏng
            from datetime import timezone
            news = md.news(test_symbols, names=names, now=datetime.now(timezone.utc))
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(news) > 0,
            data=[
                {
                    "title": n.title[:60],
                    "time": n.publish_time.strftime("%m-%d %H:%M"),
                }
                for n in news[:10]
            ],
            count=len(news),
            error="" if news else "未获取到新闻数据",
        )

    async def _test_flash_news_source(self, source: DataSource) -> CollectorResult:
        """Kiểm nguồn tin nhanh theo provider: đi qua Engine một nguồn của gói marketdata (chỉ vendor đó, không xâu chuỗi dự phòng).

        Tin nhanh là dữ liệu cấp thị trường (bản tin 7×24), không lọc theo symbols, nên không truyền test_symbols.
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._FLASH_NEWS_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 无对应 vendor，包内未实现该快讯源",
            )

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"flash_news": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        try:
            items = md.flash_news(limit=20)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "title": i.title[:80],
                    "time": i.publish_time.strftime("%m-%d %H:%M"),
                    "symbols": i.symbols,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "未获取到快讯数据",
        )

    async def _test_fundamentals_source(self, source: DataSource) -> CollectorResult:
        """Kiểm nguồn cơ bản theo provider: đi qua Engine một nguồn của gói marketdata (chỉ vendor đó, không xâu chuỗi dự phòng).

        Cơ bản là dữ liệu theo symbol (khác flash_news vốn ở cấp thị trường), nên kiểm tra
        bắt buộc phải cấu hình test_symbols tường minh, không mượn mã mặc định toàn cục;
        thiếu cấu hình thì báo error rõ ràng luôn.
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._FUNDAMENTALS_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 无对应 vendor，包内未实现该基本面源",
            )

        syms = list(source.test_symbols or [])[:5]
        if not syms:
            return CollectorResult(success=False, error="请配置测试股票代码")

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {
                    "fundamentals": [
                        SourceConfig(vendor=source.provider, config=cfg, enabled=True)
                    ]
                }
            )
        )

        try:
            items = md.fundamentals(syms)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "symbol": i.symbol,
                    "name": i.name,
                    "pe_ttm": i.pe_ttm,
                    "pb": i.pb,
                    "roe": i.roe,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "未获取到基本面数据",
        )

    async def _test_dragon_tiger_source(self, source: DataSource) -> CollectorResult:
        """Kiểm nguồn bảng giao dịch khối lớn: đi qua Engine một nguồn của gói marketdata (chỉ vendor đó, không xâu chuỗi dự phòng).

        Bảng giao dịch khối lớn là dữ liệu cấp thị trường (không lọc theo symbols), nhưng
        phải chỉ định phiên giao dịch. Khi kiểm tra thì ưu tiên lấy source.config.test_date,
        chưa cấu hình thì lấy tạm ngày hiện tại (chỉ để kiểm tra có thông không, lúc lấy
        thật vẫn theo phiên giao dịch thật).
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._DRAGON_TIGER_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 无对应 vendor，包内未实现该龙虎榜源",
            )

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {
                    "dragon_tiger": [
                        SourceConfig(vendor=source.provider, config=cfg, enabled=True)
                    ]
                }
            )
        )

        test_date = cfg.get("test_date") or datetime.now().strftime("%Y-%m-%d")

        try:
            items = md.dragon_tiger(date=test_date)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "symbol": i.symbol,
                    "name": i.name,
                    "net_buy": i.net_buy,
                }
                for i in items[:10]
            ],
            count=len(items),
            error=""
            if items
            else "未获取到龙虎榜数据（需配置 test_date 或当日有榜）",
        )

    async def _test_margin_source(self, source: DataSource) -> CollectorResult:
        """Kiểm nguồn giao dịch ký quỹ: đi qua Engine một nguồn của gói marketdata (chỉ vendor đó, không xâu chuỗi dự phòng).

        Giao dịch ký quỹ là dữ liệu theo symbol, kiểm tra bắt buộc phải cấu hình test_symbols tường minh.
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._MARGIN_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 无对应 vendor，包内未实现该融资融券源",
            )

        syms = list(source.test_symbols or [])[:5]
        if not syms:
            return CollectorResult(success=False, error="请配置测试股票代码")

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"margin": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        try:
            items = md.margin(syms)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "symbol": i.symbol,
                    "date": i.date,
                    "total_balance": i.total_balance,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "未获取到融资融券数据",
        )

    async def _test_shareholders_source(self, source: DataSource) -> CollectorResult:
        """Kiểm nguồn số tài khoản cổ đông: đi qua Engine một nguồn của gói marketdata (chỉ vendor đó, không xâu chuỗi dự phòng).

        Số tài khoản cổ đông là dữ liệu theo symbol, kiểm tra bắt buộc phải cấu hình test_symbols tường minh.
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._SHAREHOLDERS_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 无对应 vendor，包内未实现该股东户数源",
            )

        syms = list(source.test_symbols or [])[:5]
        if not syms:
            return CollectorResult(success=False, error="请配置测试股票代码")

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {
                    "shareholders": [
                        SourceConfig(vendor=source.provider, config=cfg, enabled=True)
                    ]
                }
            )
        )

        try:
            items = md.shareholders(syms)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "symbol": i.symbol,
                    "report_date": i.report_date,
                    "holder_num": i.holder_num,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "未获取到股东户数数据",
        )

    async def _test_dividend_source(self, source: DataSource) -> CollectorResult:
        """Kiểm nguồn cổ tức: đi qua Engine một nguồn của gói marketdata (chỉ vendor đó, không xâu chuỗi dự phòng).

        Cổ tức là dữ liệu theo symbol, kiểm tra bắt buộc phải cấu hình test_symbols tường minh.
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._DIVIDEND_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 无对应 vendor，包内未实现该分红源",
            )

        syms = list(source.test_symbols or [])[:5]
        if not syms:
            return CollectorResult(success=False, error="请配置测试股票代码")

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"dividend": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        try:
            items = md.dividend(syms)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "symbol": i.symbol,
                    "ex_date": i.ex_date,
                    "dividend_per_share": i.dividend_per_share,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "未获取到分红数据",
        )

    async def _test_northbound_source(self, source: DataSource) -> CollectorResult:
        """Kiểm nguồn dòng vốn bắc tiến: đi qua Engine một nguồn của gói marketdata (chỉ vendor đó, không xâu chuỗi dự phòng).

        Dòng vốn bắc tiến là dữ liệu cấp thị trường (dòng tiền 7×24), không lọc theo symbols, nên không truyền test_symbols.
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._NORTHBOUND_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 无对应 vendor，包内未实现该北向资金源",
            )

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {
                    "northbound": [
                        SourceConfig(vendor=source.provider, config=cfg, enabled=True)
                    ]
                }
            )
        )

        try:
            items = md.northbound()
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "date": i.date,
                    "hgt_net": i.hgt_net,
                    "total_net": i.total_net,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "未获取到北向资金数据",
        )


# Thực thể duy nhất toàn cục
_manager: DataCollectorManager | None = None


def get_collector_manager() -> DataCollectorManager:
    """Lấy bộ quản lý nguồn dữ liệu toàn cục"""
    global _manager
    if _manager is None:
        _manager = DataCollectorManager()
    return _manager
