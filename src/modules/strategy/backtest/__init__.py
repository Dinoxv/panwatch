"""Module kiểm thử lịch sử của PanWatch (nền móng Phase 0).

Lõi kiểm thử lịch sử theo sự kiện, nhẹ, thuần Python, không phụ thuộc bên thứ ba, phục vụ:
- Kiểm chứng trên lịch sử xem tín hiệu StrategySignalRun hiện có thể hiện ra sao trong thực tế
- Cung cấp giá trị thật cho phần IC/IR của nhân tố ở Phase 2 và cho việc điều chỉnh trọng số do kiểm thử lịch sử dẫn dắt
- Dùng chung mô hình chi phí giao dịch cổ phiếu A với mô phỏng bàn giao dịch (Phase 1)

vectorbt là hướng nâng cấp vector hóa tùy chọn về sau (xem .docs/quant-framework-comparison.md).
"""

from src.modules.strategy.backtest.cost_model import CostConfig, CostModel, DEFAULT_COST_MODEL, Fill
from src.modules.strategy.backtest.data_adapter import PriceBar, from_klines, load_price_history
from src.modules.strategy.backtest.engine import (
    Backtester,
    BacktestResult,
    BTTrade,
    Signal,
    fixed_cash_sizer,
    horizon_return,
)
from src.modules.strategy.backtest import metrics

__all__ = [
    "CostConfig",
    "CostModel",
    "DEFAULT_COST_MODEL",
    "Fill",
    "PriceBar",
    "from_klines",
    "load_price_history",
    "Backtester",
    "BacktestResult",
    "BTTrade",
    "Signal",
    "fixed_cash_sizer",
    "horizon_return",
    "metrics",
]
