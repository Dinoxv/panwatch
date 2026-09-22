"""Soi danh mục (Phase 4): chỉ đọc để phân tích mức tập trung / phân bố / rủi ro của vị thế mô phỏng.

Đối chiếu với kiểu "chỉ đọc không đặt lệnh" của PortfolioPilot — thuần đọc vị thế,
**tuyệt đối không đặt lệnh**, chỉ xuất ra phần soi và lời nhắc.
Hàm thuần diagnose_positions unit test được; diagnose_paper_portfolio thì đọc DB.
"""

from __future__ import annotations

import logging

from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import PaperTradingPosition

logger = logging.getLogger(__name__)

# Ngưỡng rủi ro (có thể đưa vào cấu hình sau)
MAX_SINGLE_WEIGHT = 0.40   # Trần tỷ trọng một vị thế
HIGH_HHI = 0.50            # Mức cao của chỉ số tập trung HHI
MAX_MARKET_WEIGHT = 0.70   # Trần tỷ trọng một thị trường
MIN_POSITIONS = 3          # Số vị thế tối thiểu để coi là đã phân tán


def herfindahl(values: list[float]) -> float:
    """Mức tập trung HHI = Σ(w_i)² (w là trọng số đã chuẩn hóa). Nằm trong [1/n, 1], càng lớn càng tập trung."""
    total = sum(values)
    if total <= 0:
        return 0.0
    return sum((v / total) ** 2 for v in values)


def diagnose_positions(positions: list[dict]) -> dict:
    """Soi bằng hàm thuần.

    positions: [{symbol, market, strategy_code, market_value, unrealized_pnl}]
    """
    if not positions:
        return {
            "position_count": 0,
            "total_market_value": 0.0,
            "hhi": 0.0,
            "max_weight": 0.0,
            "by_market": {},
            "by_strategy": {},
            "total_unrealized_pnl": 0.0,
            "alerts": [],
        }

    values = [max(0.0, float(p.get("market_value") or 0.0)) for p in positions]
    total = sum(values)
    hhi = herfindahl(values)
    max_w = (max(values) / total) if total > 0 else 0.0

    by_market: dict[str, float] = {}
    by_strategy: dict[str, float] = {}
    for p, v in zip(positions, values):
        m = p.get("market") or "?"
        s = p.get("strategy_code") or "?"
        by_market[m] = by_market.get(m, 0.0) + v
        by_strategy[s] = by_strategy.get(s, 0.0) + v

    upnl = sum(float(p.get("unrealized_pnl") or 0.0) for p in positions)

    alerts: list[str] = []
    if max_w >= MAX_SINGLE_WEIGHT:
        alerts.append(f"单仓集中度过高:最大持仓占 {max_w * 100:.0f}%")
    if hhi >= HIGH_HHI:
        alerts.append(f"组合高度集中(HHI={hhi:.2f})")
    if len(positions) < MIN_POSITIONS and total > 0:
        alerts.append(f"持仓数过少({len(positions)}),分散不足")
    if total > 0:
        for m, v in by_market.items():
            if v / total >= MAX_MARKET_WEIGHT:
                alerts.append(f"{m} 市场占比过高({v / total * 100:.0f}%)")

    return {
        "position_count": len(positions),
        "total_market_value": round(total, 2),
        "hhi": round(hhi, 4),
        "max_weight": round(max_w, 4),
        "by_market": {k: round(v, 2) for k, v in by_market.items()},
        "by_strategy": {k: round(v, 2) for k, v in by_strategy.items()},
        "total_unrealized_pnl": round(upnl, 2),
        "alerts": alerts,
    }


def diagnose_paper_portfolio() -> dict:
    """Đọc vị thế open của mô phỏng → soi danh mục (chỉ đọc)."""
    db = SessionLocal()
    try:
        rows = (
            db.query(PaperTradingPosition)
            .filter(PaperTradingPosition.status == "open")
            .all()
        )
        positions: list[dict] = []
        for p in rows:
            price = p.current_price or p.entry_price or 0.0
            market_value = float(price) * int(p.quantity or 0)
            positions.append(
                {
                    "symbol": p.stock_symbol,
                    "market": p.stock_market,
                    "strategy_code": p.strategy_code or "",
                    "market_value": market_value,
                    "unrealized_pnl": float(p.unrealized_pnl or 0.0),
                }
            )
        return diagnose_positions(positions)
    finally:
        db.close()
