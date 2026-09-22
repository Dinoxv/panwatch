"""TradingAgents 进度回调。

走一个统一回调链:
1. LangChain `BaseCallbackHandler`:捕获 LangGraph 节点、LLM 和工具的真实生命周期
2. `agent.py` 将同一个 handler 注入 `Propagator.get_graph_args(callbacks=...)`，不依赖 debug 文本解析

进度写入 PanWatch 的 `log_context`,前端轮询 `/api/agents/runs/{trace_id}/progress`
聚合返回阶段；同一文件下半部提供成本提取、预算检查和估算入口。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timezone
from typing import Any

from src.platform.observability import otel
from src.platform.observability.log_context import log_context
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import AnalysisHistory

logger = logging.getLogger(__name__)

# Tiến độ và ngân sách dùng chung một bộ cửa quan sát lượt chạy TradingAgents; vòng đời trong cơ sở dữ liệu vẫn do agent_runs quản.
__all__ = [
    "STAGES_ORDER",
    "PanWatchProgressHandler",
    "aggregate_progress",
    "check_budget",
    "estimate_cost",
    "get_today_cache_key",
]


# Ánh xạ giai đoạn mặc định: 4 chuyên viên phân tích của TradingAgents + tranh luận + quản trị rủi ro + PM
STAGES_ORDER = [
    "data_collection",
    "market_analyst",
    "social_analyst",
    "news_analyst",
    "fundamentals_analyst",
    "bull_bear_debate",
    "research_manager",
    "trader",
    "risk_judge",
    "final_decision",
]

# Tên nút LangGraph của TradingAgents 0.5.0 không ánh xạ một-một với tên giai đoạn trên giao diện.
# Bí danh gom về đây thay vì rải phép so chuỗi khắp các nhánh callback; thượng nguồn đổi tên nút thì chỉ phải sửa đúng bảng này.
NODE_STAGE_ALIASES = {
    "market_analyst": "market_analyst",
    "sentiment_analyst": "social_analyst",
    "social_analyst": "social_analyst",
    "news_analyst": "news_analyst",
    "fundamentals_analyst": "fundamentals_analyst",
    "bull_researcher": "bull_bear_debate",
    "bear_researcher": "bull_bear_debate",
    "research_manager": "research_manager",
    "trader": "trader",
    "aggressive_analyst": "risk_judge",
    "conservative_analyst": "risk_judge",
    "neutral_analyst": "risk_judge",
    "risk_judge": "risk_judge",
    "portfolio_manager": "final_decision",
    "final_decision": "final_decision",
}


try:
    from langchain_core.callbacks import BaseCallbackHandler as _LCBaseCallbackHandler
    _LANGCHAIN_AVAILABLE = True
except ImportError:  # Vẫn import được module này khi chưa cài tradingagents, để test không phụ thuộc vào nó
    _LANGCHAIN_AVAILABLE = False

    class _LCBaseCallbackHandler:  # type: ignore[no-redef]
        """Fallback stub when langchain_core 未安装。"""
        pass


class PanWatchProgressHandler(_LCBaseCallbackHandler):
    """LangChain BaseCallbackHandler 兼容的进度处理器。

    新版 langchain (1.x) 把 callbacks 字段用 pydantic 校验为 BaseCallbackHandler 实例,
    所以必须继承上游基类才能被接受。

    覆盖核心 hook:
    - on_llm_start: 某个 LLM 调用开始(可推断当前在哪个 analyst)
    - on_llm_end: LLM 调用结束,带成本
    - on_chain_start/end: LangGraph 节点切换

    P0 简单实现:把所有事件都 logger.info 出来,带 trace_id 标签。
    前端通过过滤 log_entries 表的 trace_id + event=ta_progress 拿到时间线。
    """

    def __init__(
        self,
        trace_id: str,
        agent_name: str = "tradingagents",
        cancel_event: threading.Event | None = None,
    ):
        # BaseCallbackHandler của langchain_core không nhận tham số __init__, nên gọi super trực tiếp là an toàn
        try:
            super().__init__()
        except TypeError:
            # Có bản đòi không tham số, có bản đòi có tham số — bắt dự phòng cả hai
            pass
        self.trace_id = trace_id
        self.agent_name = agent_name
        self.cancel_event = cancel_event
        self._started_at = time.monotonic()
        self._total_cost = 0.0
        self._completed_stages: set[str] = set()
        # on_chain_end của LangChain 1.x không bảo đảm mang theo name/metadata, nên bắt buộc phải lưu
        # ánh xạ run_id lúc start -> thông tin nút, thì mới quy sự kiện kết thúc về đúng giai đoạn.
        self._chain_runs: dict[str, dict[str, str]] = {}
        self._llm_runs: dict[str, dict[str, str]] = {}
        self._tool_runs: dict[str, dict[str, str]] = {}
        # Cầu nối OTel: handler được dựng ở phía bất đồng bộ (trước to_thread), chỗ này bắt lấy ngữ cảnh hiện tại
        # để callback trong luồng worker gắn được span con của nút / LLM vào dưới root span (bằng None khi tắt).
        self._otel_parent = otel.capture_context()
        self._otel_stage_spans: dict[str, Any] = {}
        self._otel_llm_span: Any = None

    @property
    def elapsed_sec(self) -> float:
        return time.monotonic() - self._started_at

    def _emit(self, stage: str, action: str, **extra):
        """写一条进度日志。前端按 trace_id + event=ta_progress 拉。"""
        if self.cancel_event is not None and self.cancel_event.is_set():
            return
        with log_context(
            trace_id=self.trace_id,
            agent_name=self.agent_name,
            event="ta_progress",
            tags={
                "stage": stage,
                "action": action,
                "elapsed_sec": round(self.elapsed_sec, 2),
                "total_cost_usd": round(self._total_cost, 6),
                **extra,
            },
        ):
            agent = extra.get("agent") or extra.get("langgraph_node") or ""
            detail = f" agent={agent}" if agent else ""
            logger.info(f"[TA进度] stage={stage} action={action}{detail} {extra}")

    def emit(self, stage: str, action: str, **extra) -> None:
        """向采集等非 LangChain 阶段发出同一格式的进度事件。"""
        self._emit(stage, action, **extra)

    # ---- Giao diện callbacks của LangChain ----

    # Điểm mấu chốt: chi phí LLM mặc định ước theo token (đơn giá deepseek-chat), phía gọi có thể tiêm đơn giá chính xác hơn sau
    _PRICE_PER_M_PROMPT = 0.14
    _PRICE_PER_M_COMPLETION = 0.28

    def on_llm_start(self, serialized, prompts, **kwargs):
        self._llm_call_count = getattr(self, "_llm_call_count", 0) + 1
        model = ""
        try:
            model = (
                (kwargs.get("invocation_params") or {}).get("model")
                or (serialized or {}).get("name")
                or ""
            )
        except Exception:
            model = ""
        agent = _callback_agent(kwargs, self._chain_runs)
        operation_id = str(kwargs.get("run_id") or f"llm:{self._llm_call_count}")
        self._llm_runs[operation_id] = {"agent": agent, "model": model}
        self._emit(
            "llm_call",
            "llm_start",
            call_n=self._llm_call_count,
            model=model,
            operation_id=operation_id,
            **({"agent": agent, "langgraph_node": agent} if agent else {}),
        )
        # OTel: mỗi lời gọi LLM của TA -> một span con gen_ai (theo quy ước ngữ nghĩa GenAI).
        self._otel_llm_span = otel.start_detached_span(
            f"chat {model}".strip() if model else "chat",
            parent_context=self._otel_parent,
            attributes={
                otel.GEN_AI_SYSTEM: "tradingagents",
                otel.GEN_AI_OPERATION_NAME: "chat",
                **({otel.GEN_AI_REQUEST_MODEL: model} if model else {}),
            },
        )

    def on_llm_end(self, response, **kwargs):
        # LLMResult.llm_output của langchain có chứa token_usage
        usage = {}
        try:
            usage = (response.llm_output or {}).get("token_usage") or {}
        except Exception:
            pass
        prompt_tokens = usage.get("prompt_tokens") or 0
        completion_tokens = usage.get("completion_tokens") or 0
        # Cộng dồn chi phí ước tính
        cost = (
            prompt_tokens / 1_000_000 * self._PRICE_PER_M_PROMPT
            + completion_tokens / 1_000_000 * self._PRICE_PER_M_COMPLETION
        )
        self.record_cost(cost)
        operation_id = str(kwargs.get("run_id") or "")
        operation = self._llm_runs.pop(operation_id, {})
        agent = operation.get("agent") or _callback_agent(kwargs, self._chain_runs)
        self._emit(
            "llm_call",
            "llm_end",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            call_cost=round(cost, 6),
            operation_id=operation_id,
            **({"agent": agent, "langgraph_node": agent} if agent else {}),
        )
        # OTel: điền ngược lượng token rồi đóng span gen_ai.
        if self._otel_llm_span is not None:
            otel.set_span_attributes(
                self._otel_llm_span,
                {
                    otel.GEN_AI_USAGE_INPUT_TOKENS: int(prompt_tokens),
                    otel.GEN_AI_USAGE_OUTPUT_TOKENS: int(completion_tokens),
                },
            )
            otel.end_span(self._otel_llm_span)
            self._otel_llm_span = None

    def on_chain_start(self, serialized, inputs, **kwargs):
        # Chuyển nút LangGraph. Tên nút ưu tiên lấy từ kwargs.name / metadata.langgraph_node,
        # vì serialized ở các bản LangChain khác nhau có khi chỉ chứa tên kiểu runnable.
        name = _callback_name(serialized, kwargs)
        stage = _normalize_stage(name)
        if not stage:
            return
        run_id = _run_id(kwargs)
        if run_id:
            self._chain_runs[run_id] = {
                "name": name,
                "stage": stage,
                "parent_run_id": _parent_run_id(kwargs),
            }
        self._emit(
            stage,
            "stage_start",
            langgraph_node=name,
            run_id=run_id,
            parent_run_id=_parent_run_id(kwargs),
        )
        # Span nút của OTel chỉ giữ một giai đoạn đang hoạt động; nút song song / thử lại trùng nhau vẫn sinh sự kiện tiến độ,
        # nhưng không để span trùng làm cây truy vết phình vô hạn.
        if stage not in self._otel_stage_spans:
            span = otel.start_detached_span(
                f"tradingagents.stage {stage}",
                parent_context=self._otel_parent,
                attributes={
                    otel.ATTR_TA_STAGE: stage,
                    otel.ATTR_AGENT_NAME: self.agent_name,
                },
            )
            if span is not None:
                self._otel_stage_spans[stage] = span

    def on_chain_end(self, outputs, **kwargs):
        self._finish_chain("stage_end", kwargs)

    def on_llm_error(self, error, **kwargs):
        self._emit(
            "llm_call",
            "llm_error",
            error=str(error)[:200],
            operation_id=str(kwargs.get("run_id") or ""),
        )
        self._emit("error", "llm_error", error=str(error)[:200])

    def on_chain_error(self, error, **kwargs):
        self._finish_chain("stage_error", kwargs, error=str(error)[:200])
        self._emit("error", "chain_error", error=str(error)[:200], run_id=_run_id(kwargs))

    def on_tool_start(self, serialized, input_str, **kwargs):
        """记录 LangGraph ToolNode 当前正在执行的工具。"""
        name = ""
        try:
            name = kwargs.get("name") or (serialized or {}).get("name") or "unknown"
        except Exception:
            name = kwargs.get("name") or "unknown"
        operation_id = str(kwargs.get("run_id") or f"tool:{name}")
        agent = _callback_agent(kwargs, self._chain_runs)
        self._tool_runs[operation_id] = {"agent": agent, "tool": str(name)}
        self._emit(
            "llm_call",
            "tool_start",
            tool=str(name),
            operation_id=operation_id,
            **({"agent": agent, "langgraph_node": agent} if agent else {}),
        )

    def on_tool_end(self, output, **kwargs):
        operation_id = str(kwargs.get("run_id") or "")
        operation = self._tool_runs.pop(operation_id, {})
        name = kwargs.get("name") or kwargs.get("tool_name") or operation.get("tool") or "unknown"
        agent = operation.get("agent") or _callback_agent(kwargs, self._chain_runs)
        self._emit(
            "llm_call",
            "tool_end",
            tool=str(name),
            operation_id=operation_id,
            **({"agent": agent, "langgraph_node": agent} if agent else {}),
        )

    def on_tool_error(self, error, **kwargs):
        operation_id = str(kwargs.get("run_id") or "")
        operation = self._tool_runs.pop(operation_id, {})
        name = kwargs.get("name") or kwargs.get("tool_name") or operation.get("tool") or "unknown"
        agent = operation.get("agent") or _callback_agent(kwargs, self._chain_runs)
        self._emit(
            "llm_call",
            "tool_error",
            tool=str(name),
            error=str(error)[:200],
            operation_id=operation_id,
            **({"agent": agent, "langgraph_node": agent} if agent else {}),
        )

    # ---- Phương thức công khai ----

    def record_cost(self, usd: float) -> None:
        self._total_cost += usd

    def _guess_stage(self, serialized: dict, kwargs: dict) -> str:
        name = _callback_name(serialized, kwargs) or "unknown"
        return _normalize_stage(name) or "unknown"

    def _finish_chain(self, action: str, kwargs: dict, **extra: Any) -> None:
        """按 run_id 找回节点并发出结束事件；上游未携带节点名时也能正确闭环。"""
        run_id = _run_id(kwargs)
        record = self._chain_runs.pop(run_id, None) if run_id else None
        name = (record or {}).get("name") or _callback_name(None, kwargs)
        stage = (record or {}).get("stage") or _normalize_stage(name)
        if not stage:
            return
        self._completed_stages.add(stage)
        self._emit(
            stage,
            action,
            langgraph_node=name,
            run_id=run_id,
            parent_run_id=(record or {}).get("parent_run_id") or _parent_run_id(kwargs),
            **extra,
        )
        if action in {"stage_end", "stage_error"}:
            span = self._otel_stage_spans.pop(stage, None)
            if span is not None:
                otel.end_span(span)


def _normalize_stage(name: str) -> str:
    """把 LangGraph 节点名标准化到 STAGES_ORDER 里的一个值。"""
    n = "_".join(str(name or "").strip().lower().replace("-", " ").split())
    if not n:
        return ""
    if n in NODE_STAGE_ALIASES:
        return NODE_STAGE_ALIASES[n]
    for stage in STAGES_ORDER:
        if stage in n:
            return stage
    return ""


def _callback_name(serialized: Any, kwargs: dict[str, Any]) -> str:
    """兼容 LangChain callback 的 name/metadata/serialized 三种节点来源。"""
    metadata = kwargs.get("metadata") or {}
    return str(
        kwargs.get("name")
        or metadata.get("langgraph_node")
        or (serialized or {}).get("name", "")
        or ""
    ).strip()


def _run_id(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("run_id") or "")


def _parent_run_id(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("parent_run_id") or "")


def _callback_agent(kwargs: dict[str, Any], chain_runs: dict[str, dict[str, str]]) -> str:
    metadata = kwargs.get("metadata") or {}
    agent = str(metadata.get("langgraph_node") or kwargs.get("name") or "").strip()
    if agent:
        return agent
    parent = chain_runs.get(_parent_run_id(kwargs))
    return str((parent or {}).get("name") or "")


def aggregate_progress(log_entries: list[dict]) -> dict:
    """读 log_entries 表里 event=ta_progress 的记录,聚合成阶段进度。

    log_entries 行结构(参考 src/web/log_handler.py):
    {timestamp, level, logger_name, message, trace_id, agent_name, event, tags, ...}
    tags 是 dict,含 stage / action / elapsed_sec / total_cost_usd 等。

    返回结构(给前端):
    {
        "current_stage": "bull_bear_debate",
        "completed_stages": [...],
        "started_at": ...,
        "elapsed_sec": 123.4,
        "total_cost_usd": 0.018,
        "stages": [
            {"name": "market_analyst", "status": "done", "duration_sec": 12.3, "cost_usd": 0.004},
            ...
        ]
    }
    """
    stage_state: dict[str, dict] = {s: {"name": s, "status": "pending"} for s in STAGES_ORDER}
    total_cost = 0.0
    current_stage = None
    active_operations: dict[str, dict] = {}
    started_at = None
    collection_sources: dict[str, dict] = {}

    for entry in log_entries:
        tags = entry.get("tags") or {}
        stage = tags.get("stage")
        action = tags.get("action") or ""
        source = tags.get("source")
        ts = entry.get("timestamp")
        if started_at is None and ts:
            started_at = ts

        if not stage or stage not in stage_state:
            # Sự kiện LLM / công cụ không phải một giai đoạn riêng, nhưng vẫn cần giữ lại thao tác đang chạy,
            # để khi request dữ liệu bên ngoài bị treo thì giao diện hiện được tên công cụ cụ thể.
            if stage == "llm_call":
                kind = "tool" if action.startswith("tool_") else "llm"
                name = tags.get("tool") if kind == "tool" else tags.get("model")
                operation_id = str(tags.get("operation_id") or f"{kind}:{name or action}")
                agent = str(tags.get("agent") or tags.get("langgraph_node") or "")
                if action == "llm_start":
                    operation = {"kind": "llm", "name": tags.get("model") or "LLM 调用"}
                    if agent:
                        operation["agent"] = agent
                    active_operations[operation_id] = operation
                elif action == "tool_start":
                    operation = {"kind": "tool", "name": tags.get("tool") or "工具调用"}
                    if agent:
                        operation["agent"] = agent
                    active_operations[operation_id] = operation
                elif action in {"llm_end", "tool_end", "llm_error", "tool_error"}:
                    if tags.get("operation_id"):
                        active_operations.pop(operation_id, None)
                    else:
                        # Tương thích nhật ký cũ / callback của thượng nguồn không gửi run_id: chỉ gỡ đúng một thao tác
                        # cùng loại cùng tên, không ảnh hưởng các công cụ khác đang chạy song song.
                        expected_name = name or ("工具调用" if kind == "tool" else "LLM 调用")
                        for key, operation in list(active_operations.items()):
                            if operation["kind"] == kind and operation["name"] == expected_name:
                                active_operations.pop(key, None)
                                break
            continue

        if stage == "data_collection" and source:
            source_state = collection_sources.setdefault(
                source,
                {"name": source, "status": "pending"},
            )
            if action == "source_start":
                source_state["status"] = "running"
            elif action == "source_end":
                source_state["status"] = "done"
            elif action == "source_error":
                source_state["status"] = "error"
                if tags.get("error"):
                    source_state["error"] = str(tags["error"])[:200]

        # Chi phí cộng dồn lấy total_cost_usd của bản ghi cuối
        cost = tags.get("total_cost_usd")
        if cost is not None:
            total_cost = max(total_cost, float(cost))

        if action == "stage_start":
            stage_state[stage]["status"] = "running"
            stage_state[stage]["started_at"] = ts
            current_stage = stage
        elif action == "stage_end":
            stage_state[stage]["status"] = "done"
            if "started_at" in stage_state[stage] and ts:
                # Thời lượng rút gọn (ts thật là datetime, chỗ này dựa vào phía gọi chuyển đổi)
                pass

    return {
        "current_stage": current_stage,
        "completed_stages": [s for s, v in stage_state.items() if v["status"] == "done"],
        "started_at": started_at,
        "elapsed_sec": float(log_entries[-1].get("tags", {}).get("elapsed_sec", 0))
        if log_entries
        else 0,
        "total_cost_usd": round(total_cost, 6),
        "active_operation": next(reversed(active_operations.values()), None)
        if active_operations
        else None,
        "stages": [stage_state[s] for s in STAGES_ORDER],
        "data_sources": list(collection_sources.values()),
    }


# ============================================================================
# Cost and budget tracking
# ============================================================================


def check_budget(monthly_budget_usd: float, agent_name: str = "tradingagents") -> dict:
    """统计本月已用美元 + 剩余,供触发前校验。

    Returns:
        {
            "used": float,           # 本月已用(美元)
            "remaining": float,      # 剩余(美元)
            "limit": float,          # 配置上限
            "exceeded": bool,        # 是否超限
            "runs_this_month": int,  # 本月运行次数
        }
    """
    now = datetime.now(timezone.utc)
    # AnalysisHistory.analysis_date là chuỗi "YYYY-MM-DD"
    month_prefix = now.strftime("%Y-%m")

    db = SessionLocal()
    try:
        records = (
            db.query(AnalysisHistory)
            .filter(
                AnalysisHistory.agent_name == agent_name,
                AnalysisHistory.analysis_date.like(f"{month_prefix}-%"),
            )
            .all()
        )

        total = 0.0
        for r in records:
            cost = _extract_cost(r.raw_data)
            if cost:
                total += cost

        used = round(total, 4)
        remaining = max(0.0, float(monthly_budget_usd) - used)
        return {
            "used": used,
            "remaining": round(remaining, 4),
            "limit": float(monthly_budget_usd),
            "exceeded": used >= float(monthly_budget_usd),
            "runs_this_month": len(records),
        }
    except Exception as e:
        logger.warning(f"[TA成本] 预算查询失败,默认放行: {e}")
        return {
            "used": 0.0,
            "remaining": float(monthly_budget_usd),
            "limit": float(monthly_budget_usd),
            "exceeded": False,
            "runs_this_month": 0,
        }
    finally:
        db.close()


def _extract_cost(raw_data) -> float:
    """从 AnalysisHistory.raw_data 提取 cost_usd。"""
    if not isinstance(raw_data, dict):
        return 0.0
    cost = raw_data.get("cost_usd")
    if cost is None:
        return 0.0
    try:
        return float(cost)
    except (TypeError, ValueError):
        return 0.0


def estimate_cost(
    *,
    debate_rounds: int,
    selected_analysts: list[str],
    model: str = "deepseek-chat",
) -> dict:
    """单次分析的成本估算(粗略,实际可能 ±50%)。

    用于触发前给用户预估。公式假设:
    - 每分析师 ~5k input + 2k output token
    - 辩论每轮 ~12k input + 4k output token
    - 风控 + PM ~15k input + 3k output token
    - LangGraph 累积上下文实际比理论高 2-5 倍
    """
    n_analysts = len(selected_analysts or [])
    prompt_tokens = n_analysts * 5000 + max(1, debate_rounds) * 12000 + 15000
    completion_tokens = n_analysts * 2000 + max(1, debate_rounds) * 4000 + 3000

    # Bảng đơn giá (USD / triệu token)
    PRICING = {
        "deepseek-chat": (0.14, 0.28),
        "deepseek-reasoner": (0.55, 2.19),
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4o": (2.50, 10.00),
        "claude-sonnet-4": (3.00, 15.00),
        "glm-4-flash": (0.05, 0.20),
    }
    input_rate, output_rate = PRICING.get(model.lower(), PRICING["deepseek-chat"])
    cost = (prompt_tokens / 1_000_000 * input_rate) + (
        completion_tokens / 1_000_000 * output_rate
    )

    return {
        "model": model,
        "prompt_tokens_est": prompt_tokens,
        "completion_tokens_est": completion_tokens,
        "cost_low_usd": round(cost * 2, 4),
        "cost_high_usd": round(cost * 5, 4),
    }


def get_today_cache_key(symbol: str, market: str, debate_rounds: int, model: str) -> str:
    """生成同标的同日的缓存键,用于跳过重复 LLM 调用。"""
    today = date.today().isoformat()
    return f"{market}:{symbol}:{today}:r{debate_rounds}:{model}"
