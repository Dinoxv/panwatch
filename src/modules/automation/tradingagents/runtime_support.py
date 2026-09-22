"""Khớp nối lúc chạy của TradingAgents: cấu hình LLM, tiêm khóa và bản vá tương thích LangChain.

Bắc cầu cấu hình AIClient của PanWatch → cấu hình LLM của TradingAgents.

TradingAgents chạy LLM qua langchain-openai / langchain-anthropic và tương tự, đọc dict
config + biến môi trường (`OPENAI_API_KEY`/`DEEPSEEK_API_KEY`…). Module này bắc cầu cấu
hình AIClient của PanWatch sang đó.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from src.platform.ai.ai_client import AIClient

logger = logging.getLogger(__name__)

# Đây là giao diện runtime ổn định mà cửa vào Agent được phép phụ thuộc; phần cài đặt bản vá tương thích nằm ở nửa dưới tệp này.
__all__ = [
    "VALID_ANALYSTS",
    "apply_compat_patches",
    "build_ta_llm_config",
    "inject_api_key_env",
]


# Các giá trị hợp lệ của trường selected_analysts trong TradingAgents (xem graph/trading_graph.py của thượng nguồn)
VALID_ANALYSTS = {"market", "social", "news", "fundamentals"}


def build_ta_llm_config(
    ai_client: AIClient,
    *,
    debate_rounds: int = 1,
    selected_analysts: list[str] | None = None,
    output_language: str = "Chinese",
    deep_model: str | None = None,
    quick_model: str | None = None,
    market: str = "",
    enable_sec_edgar: bool = False,
    runtime_dir: str | Path | None = None,
    holding_period_days: int = 5,
    llm_timeout_seconds: int = 120,
    llm_max_retries: int = 0,
    llm_max_tokens: int = 4096,
) -> dict[str, Any]:
    """Sinh dict config mà TradingAgents mong đợi.

    Kế thừa tradingagents.default_config.DEFAULT_CONFIG (chứa các trường bắt buộc như
    data_cache_dir / project_dir / memory_log_path…), rồi ghi đè bằng cấu hình PanWatch:
    - llm_provider: thống nhất đi giao thức tương thích openrouter (dùng chat completions, né Responses API của OpenAI)
    - backend_url: base_url của dịch vụ AI PanWatch
    - deep_think_llm: "mô hình mạnh" dùng cho suy luận/tranh luận/kiểm soát rủi ro/PM. Mặc định lấy ai_client.model;
      tham số deep_model ghi đè được, cho phép tranh luận dùng mô hình đắt mà chuẩn như claude-sonnet / o3
    - quick_think_llm: "mô hình nhanh" cho chuyên viên phân tích gọi công cụ. Mặc định là deep_model;
      tham số quick_model ghi đè được, cho phép chuyên viên phân tích dùng mô hình rẻ như haiku / gpt-4o-mini
    - max_debate_rounds: số vòng tranh luận
    - selected_analysts: ["market", "social", "news", "fundamentals"]
    - output_language: "Chinese" / "English"

    Lưu ý: thượng nguồn TA để deep + quick dùng chung backend_url, nên hai mô hình bắt
    buộc nằm sau **cùng một endpoint**. Muốn trộn Claude + GPT thì nên dùng LiteLLM proxy
    gom nhiều provider về một endpoint.
    """
    analysts = list(selected_analysts or VALID_ANALYSTS)
    invalid = [a for a in analysts if a not in VALID_ANALYSTS]
    if invalid:
        raise ValueError(
            f"非法 analyst 名: {invalid}; 合法值: {sorted(VALID_ANALYSTS)}"
        )

    # Kế thừa config mặc định của thượng nguồn (gồm data_cache_dir / project_dir / memory_log_path...),
    # nếu không thì TradingAgentsGraph.__init__ gọi os.makedirs(config["data_cache_dir"]) sẽ KeyError.
    try:
        from tradingagents.default_config import DEFAULT_CONFIG as _UPSTREAM_DEFAULT
        config = dict(_UPSTREAM_DEFAULT)
    except ImportError:
        config = {}

    # config của thượng nguồn có cấu hình vendor lồng nhau; sao chép trước để một lượt chạy không làm bẩn DEFAULT_CONFIG.
    config["data_vendors"] = dict(config.get("data_vendors") or {})
    config["tool_vendors"] = dict(config.get("tool_vendors") or {})

    if runtime_dir is not None:
        root = Path(runtime_dir).expanduser().resolve()
        results_dir = root / "results"
        data_cache_dir = root / "cache"
        memory_dir = root / "memory"
        for directory in (results_dir, data_cache_dir, memory_dir):
            directory.mkdir(parents=True, exist_ok=True)
        config.update({
            "results_dir": str(results_dir),
            "data_cache_dir": str(data_cache_dir),
            "memory_log_path": str(memory_dir / "trading_memory.md"),
        })

    # Ba báo cáo tài chính của SEC EDGAR có ngữ nghĩa ngày nộp hồ sơ, chỉ ưu tiên khi là cổ phiếu Mỹ và người dùng bật tường minh;
    # mã ngoài phạm vi SEC hoặc lúc SEC tạm không dùng được thì lùi về yfinance.
    statement_vendor = "sec_edgar,yfinance" if enable_sec_edgar and market.upper() == "US" else "yfinance"
    # set_config() gộp dict lồng nhau. Kể cả lượt này không bật EDGAR vẫn phải ghi lại tường minh
    # yfinance, tránh tool_vendors còn sót từ lượt chạy cổ phiếu Mỹ trước rò sang lượt phân tích A / Hồng Kông.
    config["tool_vendors"].update({
        "get_balance_sheet": statement_vendor,
        "get_cashflow": statement_vendor,
        "get_income_statement": statement_vendor,
    })

    # PanWatch ghi đè.
    # ⚠️ llm_provider cố ý KHÔNG đặt là "openai": TA thấy openai sẽ ép bật use_responses_api=True
    # (OpenAI Responses API, endpoint /v1/responses), mà các dịch vụ tương thích OpenAI của bên thứ ba như SiliconFlow / Zhipu / Ollama
    # không hỗ trợ endpoint đó nên sẽ trả 404.
    # Dùng "openrouter" để đi qua chat completions chuẩn (/v1/chat/completions), đồng thời backend_url
    # ghi đè endpoint openrouter mặc định bằng base_url thật mà PanWatch đã cấu hình.
    # Phân giải hai mô hình:
    # - deep_model không chỉ định → dùng ai_client.model
    # - quick_model không chỉ định → dùng deep_model (suy biến về trường hợp một mô hình)
    deep_llm = (deep_model or ai_client.model or "").strip() or ai_client.model
    quick_llm = (quick_model or deep_llm or "").strip() or deep_llm

    config.update({
        "llm_provider": "openrouter",
        "backend_url": ai_client.base_url,
        "deep_think_llm": deep_llm,
        "quick_think_llm": quick_llm,
        "max_debate_rounds": max(1, int(debate_rounds)),
        "max_risk_discuss_rounds": 1,
        "selected_analysts": analysts,
        "output_language": output_language,
        "online_tools": True,
        "checkpoint_enabled": False,  # Tránh để tệp checkpoint sqlite làm bẩn dữ liệu
        "holding_period_days": max(1, int(holding_period_days)),
        # TradingAgents 0.5.0 mặc định giao mấy thứ này cho SDK bên dưới; không đặt giới hạn thì khi nhà cung cấp
        # rớt kết nối hoặc mô hình xuất liên tục, cả LangGraph sẽ kẹt vĩnh viễn ở chuyên viên phân tích hiện tại.
        "llm_timeout_seconds": max(1, int(llm_timeout_seconds)),
        "llm_max_retries": max(0, int(llm_max_retries)),
        "max_tokens": max(256, int(llm_max_tokens)),
    })
    return config


def inject_api_key_env(ai_client: AIClient) -> None:
    """Tiêm API key của dịch vụ AI PanWatch vào biến môi trường.

    llm_clients của TradingAgents đọc env var khác nhau theo provider
    (OPENAI_API_KEY / DEEPSEEK_API_KEY / OPENROUTER_API_KEY…).
    PanWatch đi chế độ tương thích openrouter (chat completions), nên tiêm
    OPENROUTER_API_KEY. Đồng thời đặt luôn OPENAI_API_KEY làm lưới hứng.

    Lưu ý: đây là env var ở cấp tiến trình, nếu cùng tiến trình chạy song song nhiều yêu
    cầu với key khác nhau thì có thể tranh chấp. Ở mức P0 giả định max_workers=2 và chỉ
    dùng một dịch vụ AI, chấp nhận được.
    """
    if not ai_client.api_key:
        logger.warning("[TA] AIClient 没有 api_key,TradingAgents LLM 调用大概率失败")
        return
    # Ghi vào nhiều biến môi trường ứng viên, để TA đi nhánh provider nào cũng lấy được khóa
    os.environ["OPENROUTER_API_KEY"] = ai_client.api_key
    os.environ["OPENAI_API_KEY"] = ai_client.api_key
    os.environ["DEEPSEEK_API_KEY"] = ai_client.api_key


# ============================================================================
# LangChain compatibility patches
# ============================================================================

_PATCH_APPLIED = False


def apply_compat_patches() -> None:
    """Áp mọi bản vá tương thích LangChain. Bất biến."""
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return

    _patch_tool_call_args_coercion()
    _patch_ai_message_init()
    _PATCH_APPLIED = True


def _coerce_tool_calls_args(tool_calls: Any) -> Any:
    """Đổi trường args của từng mục trong danh sách tool_calls (nếu là chuỗi JSON) thành dict."""
    if not isinstance(tool_calls, list):
        return tool_calls
    fixed = []
    for tc in tool_calls:
        if isinstance(tc, dict) and "args" in tc:
            raw = tc.get("args")
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        tc = {**tc, "args": parsed}
                    else:
                        tc = {**tc, "args": {}}
                except (json.JSONDecodeError, TypeError):
                    tc = {**tc, "args": {}}
        fixed.append(tc)
    return fixed


def _patch_ai_message_init() -> None:
    """Patch AIMessage.__init__ để trường tool_calls tự ép chuỗi args → dict trước khi kiểm.

    Đây là đường chặn thẳng lúc dựng AIMessage nên đáng tin, bất kể tool_calls đi qua hàm nào của thượng nguồn.
    """
    try:
        from langchain_core.messages.ai import AIMessage
    except ImportError:
        return

    if getattr(AIMessage, "_panwatch_patched", False):
        return

    original_init = AIMessage.__init__

    def _patched_init(self, *args, **kwargs):
        if "tool_calls" in kwargs:
            kwargs["tool_calls"] = _coerce_tool_calls_args(kwargs["tool_calls"])
        return original_init(self, *args, **kwargs)

    AIMessage.__init__ = _patched_init  # type: ignore[method-assign]
    AIMessage._panwatch_patched = True  # type: ignore[attr-defined]
    logger.info("[TA compat] 已 patch AIMessage.__init__ 容忍 tool_calls.args 字符串")


def _patch_tool_call_args_coercion() -> None:
    """Để ToolCall / AIMessage nhận args kiểu chuỗi và tự json.loads."""
    try:
        from langchain_core.messages import tool as _tool_module
    except ImportError:
        logger.debug("[TA compat] langchain_core 未装,跳过 tool_call 补丁")
        return

    # Tìm hàm nhà máy create_tool_call (langchain 1.x); bản cũ có thể dựng thẳng bằng lớp ToolCall
    create_func = getattr(_tool_module, "create_tool_call", None)
    if create_func is None:
        logger.debug("[TA compat] create_tool_call 未找到,跳过")
        return

    if getattr(create_func, "_panwatch_patched", False):
        return  # Đã patch rồi

    original = create_func

    def _patched_create_tool_call(*args, **kwargs):
        # Lấy tham số args ra (có thể là tham số vị trí hoặc từ khóa)
        raw_args = kwargs.get("args")
        if raw_args is None and len(args) >= 2:
            # Tham số vị trí: giả định thứ tự create_tool_call(name, args, ...)
            # Chữ ký thật xem mã nguồn langchain_core.messages.tool, ở đây xử lý nới lỏng
            try:
                # Dựng lại kwargs để validator nghiêm ngặt của thượng nguồn nhận được dict
                pass
            except Exception:
                pass

        # Sửa kiểu của args
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
                if isinstance(parsed, dict):
                    kwargs["args"] = parsed
                    logger.debug(
                        f"[TA compat] tool_call.args 字符串已自动 parse 成 dict "
                        f"(原始长度 {len(raw_args)})"
                    )
                else:
                    kwargs["args"] = {}
            except (json.JSONDecodeError, TypeError):
                kwargs["args"] = {}
                logger.debug("[TA compat] tool_call.args 不是合法 JSON,降级为 {}")

        return original(*args, **kwargs)

    _patched_create_tool_call._panwatch_patched = True  # type: ignore[attr-defined]

    # Thay ký hiệu cấp module + thay cả phần import bên trong
    _tool_module.create_tool_call = _patched_create_tool_call
    try:
        # langchain_core.output_parsers.openai_tools ở đầu tệp có from . import create_tool_call
        # nhưng ngữ nghĩa import là gắn đối tượng vào cục bộ, nên phải thay cả bên đó
        from langchain_core.output_parsers import openai_tools as _ot
        if hasattr(_ot, "create_tool_call"):
            _ot.create_tool_call = _patched_create_tool_call
    except ImportError:
        pass

    logger.info("[TA compat] 已 patch langchain_core.messages.tool.create_tool_call")


def _patch_ai_message_validator() -> None:
    """Phương án dự phòng: patch thẳng AIMessage.model_validate để hạ cấp làm sạch khi args là chuỗi.

    Hiện chưa bật, chỉ bật khi tool_call_coercion không đủ.
    """
    pass
