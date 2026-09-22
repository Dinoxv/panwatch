from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from zoneinfo import ZoneInfo


class MarketCode(str, Enum):
    CN = "CN"  # Cổ phiếu A
    HK = "HK"  # Cổ phiếu Hồng Kông
    US = "US"  # Cổ phiếu Mỹ


@dataclass
class TradingSession:
    """一个交易时段"""
    start: time
    end: time


@dataclass
class MarketDef:
    """市场定义"""
    code: MarketCode
    name: str
    timezone: str
    sessions: list[TradingSession]
    symbol_pattern: str  # Biểu thức chính quy dùng để kiểm tra định dạng mã cổ phiếu

    def get_tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def is_trading_time(self, dt: datetime | None = None) -> bool:
        """判断给定时间是否在交易时段内"""
        if dt is None:
            dt = datetime.now(self.get_tz())
        else:
            dt = dt.astimezone(self.get_tz())

        # Ngày không giao dịch (cuối tuần / nghỉ lễ theo quy định của thị trường A) thì nhất loạt không giao dịch.
        # Import trễ: trading_calendar phụ thuộc MarketCode/MARKETS của chính module này.
        from src.platform.scheduling.trading_calendar import is_trading_day

        if not is_trading_day(self.code, dt.date()):
            return False

        current_time = dt.time()
        return any(
            session.start <= current_time <= session.end
            for session in self.sessions
        )


# Các thị trường định nghĩa sẵn
MARKETS: dict[MarketCode, MarketDef] = {
    MarketCode.CN: MarketDef(
        code=MarketCode.CN,
        name="A股",
        timezone="Asia/Shanghai",
        sessions=[
            TradingSession(time(9, 30), time(11, 30)),
            TradingSession(time(13, 0), time(15, 0)),
        ],
        symbol_pattern=r"^[036]\d{5}$",
    ),
    MarketCode.HK: MarketDef(
        code=MarketCode.HK,
        name="港股",
        timezone="Asia/Hong_Kong",
        sessions=[
            TradingSession(time(9, 30), time(12, 0)),
            TradingSession(time(13, 0), time(16, 0)),
        ],
        symbol_pattern=r"^\d{5}$",
    ),
    MarketCode.US: MarketDef(
        code=MarketCode.US,
        name="美股",
        timezone="America/New_York",
        sessions=[
            TradingSession(time(9, 30), time(16, 0)),
        ],
        symbol_pattern=r"^[A-Z]{1,5}$",
    ),
}


@dataclass
class StockData:
    """标准化行情数据"""
    symbol: str
    name: str
    market: MarketCode
    current_price: float
    change_pct: float       # Biên độ %
    change_amount: float    # Mức tăng giảm tuyệt đối
    volume: float           # Khối lượng (lô)
    turnover: float         # Giá trị giao dịch (đồng)
    open_price: float
    high_price: float
    low_price: float
    prev_close: float
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class IndexData:
    """大盘指数数据"""
    symbol: str
    name: str
    market: MarketCode
    current_price: float
    change_pct: float
    change_amount: float
    volume: float
    turnover: float
    timestamp: datetime = field(default_factory=datetime.now)
