"""请求 / 响应 / 行情数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Request:
    """一次数据请求。frozen=True 便于做缓存键。"""

    symbols: tuple[str, ...] = ()
    market: str = "CN"
    timeframe: str = "day"
    limit: int = 120
    since_hours: int = 12
    extra: tuple[tuple[str, Any], ...] = ()

    def cache_key(self, datatype: str) -> str:
        sym = ",".join(self.symbols)
        extra = ",".join(f"{k}={v}" for k, v in self.extra)
        return f"{datatype}|{self.market}|{self.timeframe}|{self.limit}|{self.since_hours}|{sym}|{extra}"


@dataclass
class Quote:
    """标准化实时报价。字段对齐 _parse_tencent_line 的产出。"""

    symbol: str
    market: str
    current_price: float
    name: str = ""
    prev_close: float | None = None
    open_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    change_amount: float | None = None
    change_pct: float | None = None
    volume: float | None = None
    turnover: float | None = None
    turnover_rate: float | None = None
    volume_ratio: float | None = None
    pe_ratio: float | None = None
    circulating_market_value: float | None = None
    total_market_value: float | None = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Bar:
    """标准化日K(对齐 PanWatch KlineData:date/open/close/high/low/volume)。"""

    date: str
    open: float
    close: float
    high: float
    low: float
    volume: float = 0.0


@dataclass
class CapitalFlow:
    """资金流向(对齐 PanWatch src/collectors/capital_flow_collector.CapitalFlow)。"""

    symbol: str
    name: str
    main_net_inflow: float | None = None      # Dòng tiền lớn vào ròng
    main_net_inflow_pct: float | None = None   # Tỷ trọng dòng tiền lớn vào ròng
    super_net_inflow: float | None = None      # Lệnh siêu lớn vào ròng
    big_net_inflow: float | None = None        # Lệnh lớn vào ròng
    mid_net_inflow: float | None = None        # Lệnh vừa vào ròng
    small_net_inflow: float | None = None      # Lệnh nhỏ vào ròng
    main_net_5d: float | None = None           # Dòng tiền lớn vào ròng 5 phiên


@dataclass(frozen=True)
class HotStock:
    """热门/异动股(对齐 PanWatch src/collectors/discovery_collector.HotStock)。"""

    symbol: str
    market: str
    name: str
    price: float | None
    change_pct: float | None
    turnover: float | None
    volume: float | None


@dataclass(frozen=True)
class HotBoard:
    """热门板块(对齐 PanWatch src/collectors/discovery_collector.HotBoard)。"""

    code: str
    name: str
    change_pct: float | None
    change_amount: float | None
    turnover: float | None


@dataclass
class EventItem:
    """结构化事件(对齐 PanWatch src/collectors/events_collector.EventItem)。"""

    source: str
    external_id: str
    event_type: str
    title: str
    publish_time: datetime
    symbols: list[str]
    importance: int
    url: str


@dataclass
class Fundamentals:
    """标准化基本面/财务数据(按 symbol)。估值类字段/财报类字段来源不同、可能分批到位,
    拿不到的字段一律 None,不伪造。"""

    symbol: str
    market: str
    name: str = ""
    # —— Nhóm định giá ——
    pe_ttm: float | None = None                    # P/E (TTM)
    pe_static: float | None = None                  # P/E (tĩnh)
    pb: float | None = None                         # P/B
    ps_ttm: float | None = None                     # P/S (TTM)
    total_market_value: float | None = None         # Vốn hóa (trăm triệu)
    circulating_market_value: float | None = None   # Vốn hóa lưu hành (trăm triệu)
    dividend_yield: float | None = None             # Tỷ suất cổ tức (%)
    total_shares: float | None = None               # Tổng số cổ phần (cổ phiếu)
    float_shares: float | None = None                # Số cổ phần lưu hành (cổ phiếu)
    # —— Nhóm báo cáo tài chính ——
    eps: float | None = None                        # Lợi nhuận trên mỗi cổ phần (EPS)
    bps: float | None = None                        # Giá trị sổ sách trên mỗi cổ phần
    roe: float | None = None                        # Tỷ suất lợi nhuận trên vốn chủ sở hữu (%)
    revenue: float | None = None                    # Doanh thu
    net_profit: float | None = None                 # Lợi nhuận sau thuế thuộc cổ đông công ty mẹ
    gross_margin: float | None = None               # Biên lợi nhuận gộp (%)
    net_margin: float | None = None                 # Biên lợi nhuận ròng (%)
    revenue_yoy: float | None = None                # Tăng trưởng doanh thu so với cùng kỳ (%)
    net_profit_yoy: float | None = None             # Tăng trưởng lợi nhuận ròng so với cùng kỳ (%)
    report_date: str = ""                           # Kỳ báo cáo (giữ nguyên chuỗi, không bóc thành ngày)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class DragonTigerItem:
    """龙虎榜(东财每日龙虎榜明细,市场级,按 date 过滤)。字段待实抓校准。"""

    trade_date: str
    symbol: str
    name: str = ""
    reason: str | None = None          # Lý do lọt bảng
    close: float | None = None         # Giá đóng cửa
    change_pct: float | None = None    # Biên độ (%)
    net_buy: float | None = None       # Giá trị mua ròng trên bảng giao dịch đột biến (đồng)
    buy_amt: float | None = None       # Giá trị mua trên bảng giao dịch đột biến (đồng)
    sell_amt: float | None = None      # Giá trị bán trên bảng giao dịch đột biến (đồng)
    turnover_pct: float | None = None  # Tỷ lệ vòng quay (%)


@dataclass
class MarginItem:
    """融资融券(东财 datacenter,按 symbol,取最新一条快照)。字段待实抓校准。"""

    date: str
    symbol: str
    rz_balance: float | None = None     # Dư nợ vay mua (đồng)
    rz_buy: float | None = None         # Giá trị mua bằng vốn vay (đồng)
    rz_repay: float | None = None       # Giá trị trả nợ vay mua (đồng)
    rq_balance: float | None = None     # Dư nợ vay chứng khoán (đồng)
    rq_sell_vol: float | None = None    # Khối lượng bán khống (cổ phiếu)
    rq_repay_vol: float | None = None   # Khối lượng hoàn trả chứng khoán vay (cổ phiếu)
    total_balance: float | None = None  # Tổng dư nợ giao dịch ký quỹ (đồng)


@dataclass
class ShareholderItem:
    """股东户数(东财 datacenter,按 symbol,取最新一期)。字段待实抓校准。"""

    report_date: str
    symbol: str
    holder_num: int | None = None      # Số lượng cổ đông
    change_num: int | None = None      # Thay đổi số cổ đông (so với kỳ trước)
    change_ratio: float | None = None  # Thay đổi số cổ đông so với kỳ liền trước (%)
    avg_shares: float | None = None    # Số cổ phần bình quân mỗi cổ đông


@dataclass
class DividendItem:
    """分红(东财 datacenter,按 symbol,返回该只全部历史)。字段待实抓校准。"""

    ex_date: str
    symbol: str
    dividend_per_share: float | None = None  # Cổ tức trên mỗi cổ phần (trước thuế, đồng)
    transfer_ratio: float | None = None      # Cổ phiếu thưởng từ thặng dư vốn trên mỗi 10 cổ phần
    bonus_ratio: float | None = None         # Cổ phiếu thưởng từ lợi nhuận trên mỗi 10 cổ phần
    progress: str = ""                       # Tiến độ phương án


@dataclass
class NorthboundItem:
    """北向资金(同花顺 hexin 当日分钟累计净买入,市场级,取当日末值快照)。
    字段待实抓校准(沙箱代理拦截,无法验证真实响应结构)。"""

    date: str
    hgt_net: float | None = None   # Mua ròng qua kênh Thượng Hải (trăm triệu đồng)
    sgt_net: float | None = None   # Mua ròng qua kênh Thâm Quyến (trăm triệu đồng) ⚠️ gần đây không đáng tin (có thể NaN / sai độ lớn), phải bắt lỗi
    total_net: float | None = None  # Tổng dòng vốn phía Bắc = hgt_net + sgt_net; một trong hai là None thì kết quả là None (không bịa số)
    time: str = ""                  # Mốc phút ứng với giá trị cuối (tùy chọn)


@dataclass
class FlashNews:
    """快讯(7×24,对齐 cls/sina/eastmoney 快讯流)。市场级,symbols 可空。"""

    source: str
    external_id: str
    title: str
    content: str
    publish_time: datetime
    symbols: list[str] = field(default_factory=list)
    importance: int = 0
    url: str = ""


@dataclass
class NewsArticle:
    """新闻资讯(个股新闻+公告,对齐 PanWatch src/collectors/news_collector.NewsItem)。
    来源可为 xueqiu(雪球个股新闻)/ eastmoney_news(东财个股新闻搜索)/ eastmoney(东财公告)。"""

    source: str
    external_id: str
    title: str
    content: str
    publish_time: datetime
    symbols: list[str] = field(default_factory=list)
    importance: int = 0
    url: str = ""


@dataclass
class Response:
    """Engine 返回:承载 payload + 命中的 vendor/延迟。"""

    ok: bool
    data: Any = None
    error: str = ""
    vendor: str = ""
    latency_ms: int = 0

    @property
    def is_empty(self) -> bool:
        if self.data is None:
            return True
        if isinstance(self.data, (list, tuple, dict, set)) and len(self.data) == 0:
            return True
        return False
