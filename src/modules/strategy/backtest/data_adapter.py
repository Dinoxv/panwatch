"""Khớp dữ liệu cho kiểm thử lịch sử: KlineCollector → PriceBar, canh theo lịch giao dịch.

- PriceBar định nghĩa ở đầu module này, và **không import KlineCollector ở đầu tệp** (import muộn),
  để lõi kiểm thử lịch sử và unit test không bị ghép với httpx/thư viện mạng, chạy ngoại tuyến được.
- Thứ KlineCollector trả về vốn đã là nến ngày điều chỉnh xuôi (qfq), ngày đình chỉ tự nhiên không có bar, nên lịch giao dịch = chuỗi bar thực tế.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceBar:
    """Một cây nến ngày (điều chỉnh xuôi)."""

    date: str  # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: float


def from_klines(klines) -> list[PriceBar]:
    """Danh sách KlineData → danh sách PriceBar xếp theo ngày tăng dần."""
    out: list[PriceBar] = []
    for k in klines or []:
        try:
            out.append(
                PriceBar(
                    date=str(k.date)[:10],
                    open=float(k.open),
                    high=float(k.high),
                    low=float(k.low),
                    close=float(k.close),
                    volume=float(k.volume or 0),
                )
            )
        except Exception:
            continue
    out.sort(key=lambda b: b.date)
    return out


def load_price_history(symbol: str, market, days: int = 250) -> list[PriceBar]:
    """Đi qua KlineCollector kéo lịch sử (import muộn, tránh ghép thư viện mạng ở đầu tệp)."""
    from src.platform.marketdata.collectors.kline_collector import KlineCollector
    from src.platform.marketdata.models import MarketCode

    try:
        mc = market if isinstance(market, MarketCode) else MarketCode(str(market).upper())
    except Exception:
        mc = MarketCode.CN
    try:
        klines = KlineCollector(mc).get_klines(symbol, days=days)
    except Exception as e:
        logger.warning(f"[回测] 拉取 {symbol} K线失败: {e}")
        return []
    return from_klines(klines)


def first_index_after(bars: list[PriceBar], date: str) -> int | None:
    """Trả về chỉ số của bar đầu tiên có date lớn hơn hẳn ngày cho trước (phiên giao dịch kế tiếp, chống look-ahead)."""
    for i, b in enumerate(bars):
        if b.date > date:
            return i
    return None
