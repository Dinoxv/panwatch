"""Lõi kiểm thử lịch sử theo sự kiện, nhẹ (thuần Python, không phụ thuộc bên thứ ba).

Nhiệm vụ: cho trước tín hiệu + nến lịch sử → mô phỏng «vào lệnh lúc mở cửa phiên sau
tín hiệu, mỗi phiên kiểm cắt lỗ/chốt lời/đóng khi tới hạn», trừ chi phí giao dịch cổ
phiếu A, rồi xuất ra từng lệnh, đường giá trị ròng và các chỉ tiêu hiệu quả.

Đánh đổi thiết kế (Phase 0):
- Vào lệnh: **giá mở cửa của phiên giao dịch kế tiếp** sau ngày tín hiệu (không có hàm nhìn trước tương lai); từ T+1 mới đóng được (đúng lệ cổ phiếu A).
- Đóng lệnh (event): mỗi phiên kiểm cắt lỗ/chốt lời; cùng phiên chạm cả hai thì thận trọng xử là cắt lỗ trước; tới số phiên nắm giữ tối đa thì đóng theo giá đóng cửa.
- Nhảy giá: mở cửa đã vượt qua mức cắt lỗ/chốt lời thì khớp theo giá mở cửa (gap).
- Tỷ trọng: mặc định mỗi lệnh một lượng vốn danh nghĩa cố định, mua cổ phiếu A theo bội số 100 cổ (tiêm sizer được để Phase 1 thay).
- Đường giá trị ròng: cộng dồn lãi lỗ đã thực hiện theo ngày đóng lệnh (đơn giản hóa); phần mark lãi lỗ tạm tính theo ngày cho nhiều vị thế song song để mở rộng sau.
- Ràng buộc không khớp được khi chạm trần/sàn chưa mô hình hóa (TODO: cần giá tham chiếu + xét nhóm ngành).

Còn cung cấp horizon_return(): sao lại khẩu độ của strategy_engine.evaluate_strategy_outcomes, dùng để đối chiếu chéo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from src.platform.marketdata.bars import (
    base_index_on_or_before,
    bar_after_n_trading_days,
    close_series,
)
from src.modules.strategy.backtest import metrics as M
from src.modules.strategy.backtest.cost_model import CostModel
from src.modules.strategy.backtest.data_adapter import PriceBar, first_index_after

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Signal:
    """Một tín hiệu chờ kiểm thử lịch sử (khớp các trường chạy được của StrategySignalRun)."""

    symbol: str
    market: str
    signal_date: str                  # YYYY-MM-DD (ngày phát tín hiệu)
    entry_price: float | None = None  # None = dùng giá mở cửa phiên giao dịch kế tiếp
    stop_loss: float | None = None
    target_price: float | None = None
    holding_days: int = 10            # Số phiên giao dịch nắm giữ tối đa (chế độ event)


@dataclass
class BTTrade:
    symbol: str
    market: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    fees: float
    exit_reason: str  # stop_loss | target | expire | eod
    holding_bars: int


@dataclass
class BacktestResult:
    trades: list[BTTrade]
    equity_curve: list[float]
    equity_dates: list[str]
    metrics: dict
    initial_capital: float
    skipped: int = 0


PositionSizer = Callable[[float], int]  # price -> qty


def fixed_cash_sizer(cash_per_trade: float, lot: int = 100) -> PositionSizer:
    """Mỗi lệnh một lượng vốn danh nghĩa cố định, mua theo bội số của lot."""

    def _size(price: float) -> int:
        if price <= 0:
            return 0
        lots = int((cash_per_trade / price) // lot)
        return max(0, lots * lot)

    return _size


def _parse_day(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


class Backtester:
    def __init__(
        self,
        cost_model: CostModel | None = None,
        initial_capital: float = 1_000_000.0,
        cash_per_trade: float = 100_000.0,
        lot: int = 100,
        sizer: PositionSizer | None = None,
    ) -> None:
        self.cost = cost_model or CostModel()
        self.initial_capital = float(initial_capital)
        self.sizer = sizer or fixed_cash_sizer(cash_per_trade, lot)

    def run_single(self, signal: Signal, bars: list[PriceBar]) -> BTTrade | None:
        """Kiểm thử lịch sử cho một tín hiệu: vào lệnh lúc mở cửa phiên kế tiếp, mỗi phiên kiểm cắt lỗ/chốt lời/đóng khi tới hạn."""
        if not bars:
            return None
        ei = first_index_after(bars, signal.signal_date)
        if ei is None or ei >= len(bars):
            return None
        entry_bar = bars[ei]
        entry_price = entry_bar.open if signal.entry_price is None else float(signal.entry_price)
        if entry_price <= 0:
            return None
        qty = self.sizer(entry_price)
        if qty <= 0:
            return None

        stop = signal.stop_loss
        target = signal.target_price
        max_hold = max(1, int(signal.holding_days or 10))

        exit_price = exit_date = exit_reason = None
        held = 0
        # Kiểm tra từng phiên kể từ T+1 (ngày vào lệnh không được bán)
        for j in range(ei + 1, len(bars)):
            held = j - ei
            bar = bars[j]
            if stop and stop > 0:
                if bar.open <= stop:  # Nhảy giá thủng xuống
                    exit_price, exit_date, exit_reason = bar.open, bar.date, "stop_loss"
                    break
                if bar.low <= stop:
                    exit_price, exit_date, exit_reason = stop, bar.date, "stop_loss"
                    break
            if target and target > 0:
                if bar.open >= target:  # Nhảy giá vọt lên
                    exit_price, exit_date, exit_reason = bar.open, bar.date, "target"
                    break
                if bar.high >= target:
                    exit_price, exit_date, exit_reason = target, bar.date, "target"
                    break
            if held >= max_hold:
                exit_price, exit_date, exit_reason = bar.close, bar.date, "expire"
                break

        if exit_price is None:
            last = bars[-1]
            exit_price, exit_date, exit_reason = last.close, last.date, "eod"
            held = len(bars) - 1 - ei

        rt = self.cost.round_trip_pnl(entry_price, exit_price, qty)
        return BTTrade(
            symbol=signal.symbol,
            market=signal.market,
            entry_date=entry_bar.date,
            entry_price=round(entry_price, 4),
            exit_date=exit_date,
            exit_price=round(exit_price, 4),
            quantity=qty,
            pnl=rt["pnl"],
            pnl_pct=rt["pnl_pct"],
            fees=rt["total_cost"],
            exit_reason=exit_reason,
            holding_bars=held,
        )

    def run(
        self, signals: list[Signal], bars_by_symbol: dict
    ) -> BacktestResult:
        """Kiểm thử lịch sử hàng loạt, gộp đường giá trị ròng và các chỉ tiêu hiệu quả.

        bars_by_symbol: khóa có thể là (symbol, market) hoặc symbol.
        """
        trades: list[BTTrade] = []
        skipped = 0
        for sig in signals:
            bars = bars_by_symbol.get((sig.symbol, sig.market)) or bars_by_symbol.get(sig.symbol)
            if not bars:
                skipped += 1
                continue
            t = self.run_single(sig, bars)
            if t is None:
                skipped += 1
                continue
            trades.append(t)

        trades_sorted = sorted(trades, key=lambda t: t.exit_date)
        equity = self.initial_capital
        curve = [self.initial_capital]
        dates = [""]
        for t in trades_sorted:
            equity += t.pnl
            curve.append(round(equity, 4))
            dates.append(t.exit_date)

        pnls = [t.pnl for t in trades]
        return BacktestResult(
            trades=trades,
            equity_curve=curve,
            equity_dates=dates,
            metrics=M.summarize(curve, pnls),
            initial_capital=self.initial_capital,
            skipped=skipped,
        )


def horizon_return(signal: Signal, bars: list[PriceBar], horizon_days: int) -> float | None:
    """Sao lại đúng khẩu độ của strategy_engine.evaluate_strategy_outcomes, dùng để đối chiếu chéo.

    base = signal.entry_price; outcome = giá đóng cửa sau đúng `horizon_days`
    **phiên giao dịch** kể từ phiên gần nhất không muộn hơn signal_date;
    return% = (outcome - base) / base * 100.

    Đếm theo phiên chứ không theo ngày tự nhiên: chuỗi bar chính là lịch giao
    dịch, nên cuối tuần, nghỉ lễ và phiên đình chỉ không ăn vào horizon. Trả None
    khi chuỗi chưa đủ phiên để chốt.
    """
    snap = _parse_day(signal.signal_date)
    base = signal.entry_price
    if snap is None or not bars or not base or base <= 0:
        return None
    rows = close_series(bars)
    base_index = base_index_on_or_before(rows, snap)
    if base_index is None:
        return None
    bar = bar_after_n_trading_days(rows, base_index, horizon_days)
    if bar is None:
        return None
    return (bar[1] - base) / base * 100.0
