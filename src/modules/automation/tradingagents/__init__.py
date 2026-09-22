"""Module tích hợp TradingAgents.

Khớp TauricResearch/TradingAgents (khung quyết định đầu tư nhiều Agent, 76k sao) vào PanWatch:
- Bắc cầu AI Service của PanWatch sang cấu hình LLM của TradingAgents
- Tiêm dữ liệu của hệ Provider PanWatch vào tầng data vendor của TradingAgents (riêng cổ phiếu A)
- Ánh xạ final_state của TradingAgents thành AnalysisResult của PanWatch
- Phản hồi tiến độ qua callbacks của LangChain

Phụ thuộc mềm: thư viện `tradingagents` không có trên PyPI, người dùng phải tự git clone
+ pip install -e. Chưa cài thì TradingAgentsAgent.run() trả về lỗi rõ ràng, không làm sập dịch vụ.

Thiết kế chi tiết: `.docs/tradingagents/02-technical-design.md`
"""

from src.modules.automation.tradingagents.agent import TradingAgentsAgent
from src.modules.automation.tradingagents.data_context import (
    build_stock_metadata_context,
    patch_instrument_context,
    to_tradingagents_portfolio,
)
from src.modules.automation.tradingagents.decision import map_state_to_result
from src.modules.automation.tradingagents.observability import (
    PanWatchProgressHandler,
    aggregate_progress,
    check_budget,
    estimate_cost,
)

__all__ = [
    "TradingAgentsAgent",
    "PanWatchProgressHandler",
    "aggregate_progress",
    "build_stock_metadata_context",
    "check_budget",
    "estimate_cost",
    "map_state_to_result",
    "patch_instrument_context",
    "to_tradingagents_portfolio",
]
