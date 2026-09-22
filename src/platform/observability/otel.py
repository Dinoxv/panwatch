"""Tầng xuất OpenTelemetry (tùy chọn, mặc định tắt).

**Không đụng** tới hệ quan sát tự dựng của PanWatch (``log_context`` / ``agent_runs`` /
``tradingagents.observability``), chỉ gắn thêm một tầng xuất OTel chuẩn, để cả "bản tự
dựng + ngăn xếp chuẩn" đều có bằng chứng. Ba chỗ bắc cầu:

- Một lượt chạy Agent      -> root span (dùng lại ``trace_id`` của ``agent_runs`` để liên kết)
- Một lời gọi LLM          -> span con gen_ai (dùng lại lượng token mà ``ai_client`` đã có)
- Nút TradingAgents        -> span con (dùng lại sự kiện nút/LLM của ``observability.py``)

Nguyên tắc thiết kế (dự án sản xuất, thêm dần và lùi lại được):

1. **Mặc định không tác dụng phụ**: chưa cấu hình ``OTEL_EXPORTER_OTLP_ENDPOINT``, hoặc
   chưa cài SDK opentelemetry, thì ``init_otel()`` trả về False luôn, mọi giao diện span
   sau đó hạ xuống no-op — không ném lỗi, không kéo thêm phụ thuộc lúc chạy, không đổi
   bất kỳ hành vi sẵn có nào.
2. **Bắc cầu mỏng**: chỉ bọc thêm một context manager ở những chỗ đã cắm mốc; bản thân
   chỗ cắm mốc không biết gì về chi tiết OTel.
3. **Nạp lười**: đầu module này **không** import opentelemetry, chỉ khi ``init_otel()``
   được gọi và endpoint đã cấu hình thì mới thử import, nên
   ``import src.platform.observability.otel`` luôn an toàn, không tốn gì.

Giao ước ngữ nghĩa GenAI (OpenTelemetry Semantic Conventions for Generative AI) giúp span
được các APM chuẩn như Jaeger / Tempo / Langfuse (OTLP) nhận ra ngay là "một lời gọi mô hình".
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)


# ---- Tên thuộc tính theo quy ước ngữ nghĩa GenAI --------------------------
# Tham chiếu: OpenTelemetry Semantic Conventions for Generative AI
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"

# Thuộc tính riêng của PanWatch (bắc cầu sang mô hình trace tự dựng, để đối chiếu với agent_runs trong APM)
ATTR_AGENT_NAME = "panwatch.agent.name"
ATTR_TRACE_ID = "panwatch.trace_id"
ATTR_TRIGGER_SOURCE = "panwatch.trigger_source"
ATTR_TA_STAGE = "panwatch.tradingagents.stage"

_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "panwatch")
_INSTRUMENTATION_SCOPE = "panwatch.otel"

# Trạng thái cấp module (thực thể duy nhất trong một tiến trình)
_enabled: bool = False
_initialized: bool = False
_provider: Any = None
_tracer: Any = None


def is_enabled() -> bool:
    """Việc xuất OTel hiện đã bật chưa (endpoint đã cấu hình, SDK dùng được và khởi tạo thành công)."""
    return _enabled


def _import_sdk():
    """Thử import SDK OTel. Chưa cài thì trả None (hạ cấp êm)."""
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        return trace, Resource, TracerProvider, BatchSpanProcessor
    except Exception:  # pragma: no cover - chỉ chạm tới khi chưa cài SDK
        return None


def _build_otlp_exporter():
    """Dựng span exporter OTLP.

    Ưu tiên HTTP (``proto/http``, cổng quy ước 4318), lùi về gRPC (``proto/grpc``, 4317).
    Cả hai đều tự đọc các biến môi trường chuẩn như ``OTEL_EXPORTER_OTLP_ENDPOINT``, nên ở
    đây không truyền endpoint tường minh, để SDK tự đọc theo giao ước chuẩn (nguyên tắc ít bất ngờ nhất).
    """
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        return OTLPSpanExporter()
    except Exception:
        pass
    try:  # pragma: no cover - phụ thuộc môi trường
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as GrpcOTLPSpanExporter,
        )

        return GrpcOTLPSpanExporter()
    except Exception:
        return None


def init_otel(*, force: bool = False) -> bool:
    """Khởi tạo việc xuất OTel từ biến môi trường. Bất biến; trả về có bật thành công không.

    Chỉ thật sự bật khi ``OTEL_EXPORTER_OTLP_ENDPOINT`` khác rỗng **và** import được cả
    SDK/exporter opentelemetry; thiếu bên nào cũng im lặng hạ xuống no-op (không ảnh
    hưởng bản triển khai sẵn có).
    """
    global _enabled, _initialized, _provider, _tracer

    if _initialized and not force:
        return _enabled

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        # Chưa cấu hình endpoint — mặc định tắt, không gây tác dụng phụ nào.
        _initialized = True
        _enabled = False
        return False

    mods = _import_sdk()
    if mods is None:
        logger.warning(
            "OTEL_EXPORTER_OTLP_ENDPOINT 已配置(%s),但未安装 opentelemetry SDK,"
            "OTel 导出跳过。安装: pip install -r requirements-otel.txt",
            endpoint,
        )
        _initialized = True
        _enabled = False
        return False

    trace, Resource, TracerProvider, BatchSpanProcessor = mods
    exporter = _build_otlp_exporter()
    if exporter is None:
        logger.warning(
            "opentelemetry SDK 已装但缺少 OTLP exporter,OTel 导出跳过。"
            "安装: pip install -r requirements-otel.txt"
        )
        _initialized = True
        _enabled = False
        return False

    try:
        resource = Resource.create({"service.name": _SERVICE_NAME})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        # Đặt làm provider toàn cục (để lan truyền ngữ cảnh); việc tạo span vẫn đi qua tracer mà module này giữ.
        trace.set_tracer_provider(provider)
        _provider = provider
        _tracer = provider.get_tracer(_INSTRUMENTATION_SCOPE)
        _enabled = True
        _initialized = True
        logger.info("OTel 导出已启用,endpoint=%s service=%s", endpoint, _SERVICE_NAME)
        return True
    except Exception as e:  # pragma: no cover - bắt dự phòng lỗi khởi tạo
        logger.warning("OTel 初始化失败,降级为 no-op: %s", e)
        _enabled = False
        _initialized = True
        return False


# ---- Dành cho kiểm thử: xuất đồng bộ bằng InMemorySpanExporter ------------

def install_test_exporter():
    """Chỉ dùng cho test: đặt lại và cài InMemorySpanExporter (SimpleSpanProcessor xuất đồng bộ).

    Trả về thực thể exporter, gọi thẳng ``get_finished_spans()`` để assert. Mã sản xuất không được gọi.
    """
    global _enabled, _initialized, _provider, _tracer

    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    resource = Resource.create({"service.name": _SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # Trong kiểm thử có thể cài lại nhiều lần: ghi đè thẳng provider/tracer mà module này giữ;
    # provider toàn cục chỉ đặt ở lần đầu (OTel không cho ghi đè, đặt lại sẽ cảnh báo), nên không ép đặt toàn cục.
    try:
        trace.set_tracer_provider(provider)
    except Exception:
        pass
    _provider = provider
    _tracer = provider.get_tracer(_INSTRUMENTATION_SCOPE)
    _enabled = True
    _initialized = True
    return exporter


def reset() -> None:
    """Đặt lại trạng thái module (dùng khi teardown test)."""
    global _enabled, _initialized, _provider, _tracer
    _enabled = False
    _initialized = False
    _provider = None
    _tracer = None


# ---- Giao diện span (tất cả đều no-op khi tắt) ---------------------------

@contextmanager
def agent_run_span(
    agent_name: str,
    trace_id: str = "",
    trigger_source: str = "",
) -> Iterator[Any]:
    """root span của một lượt chạy Agent. Khi tắt thì no-op (yield None).

    Dùng lại ``trace_id`` của ``agent_runs`` làm thuộc tính span, để trong APM dễ khớp với bảng run.
    """
    if not _enabled or _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(f"agent.run {agent_name}") as span:
        try:
            span.set_attribute(ATTR_AGENT_NAME, agent_name)
            if trace_id:
                span.set_attribute(ATTR_TRACE_ID, trace_id)
            if trigger_source:
                span.set_attribute(ATTR_TRIGGER_SOURCE, trigger_source)
        except Exception:
            pass
        yield span


class _LLMSpan:
    """Tay cầm mỏng của span gen_ai: sau khi lời gọi trả về thì điền ngược lượng token/mô hình phản hồi."""

    __slots__ = ("_span",)

    def __init__(self, span: Any):
        self._span = span

    def set_response(
        self,
        *,
        model: Optional[str] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
    ) -> None:
        if self._span is None:
            return
        try:
            if model:
                self._span.set_attribute(GEN_AI_RESPONSE_MODEL, model)
            if input_tokens is not None:
                self._span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, int(input_tokens))
            if output_tokens is not None:
                self._span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, int(output_tokens))
        except Exception:
            pass


@contextmanager
def llm_span(
    model: str,
    *,
    system: str = "openai",
    operation: str = "chat",
) -> Iterator[_LLMSpan]:
    """span con gen_ai cho một lời gọi LLM. Khi tắt thì yield một tay cầm no-op.

    Tên span theo giao ước GenAI ``{operation} {model}``; thuộc tính phía yêu cầu ghi lúc
    vào, phía phản hồi (token/mô hình phản hồi) do bên gọi điền ngược qua tay cầm trả về sau khi có usage.
    """
    if not _enabled or _tracer is None:
        yield _LLMSpan(None)
        return
    span_name = f"{operation} {model}".strip() if model else operation
    with _tracer.start_as_current_span(span_name) as span:
        try:
            span.set_attribute(GEN_AI_SYSTEM, system)
            span.set_attribute(GEN_AI_OPERATION_NAME, operation)
            if model:
                span.set_attribute(GEN_AI_REQUEST_MODEL, model)
        except Exception:
            pass
        yield _LLMSpan(span)


def capture_context() -> Any:
    """Bắt ngữ cảnh OTel hiện tại (để truyền quan hệ root span qua luồng khác). Khi tắt thì trả None.

    TradingAgents chạy đồng bộ trong ``asyncio.to_thread``, ngữ cảnh OTel không tự đi qua
    luồng, nên phải bắt ở phía bất đồng bộ rồi truyền tường minh sang luồng worker làm parent.
    """
    if not _enabled:
        return None
    try:
        from opentelemetry import context as otel_context

        return otel_context.get_current()
    except Exception:
        return None


def start_detached_span(
    name: str,
    *,
    parent_context: Any = None,
    attributes: Optional[dict] = None,
) -> Any:
    """Mở một span "rời" (không đặt làm current, phải tự ``end``). Khi tắt thì trả None.

    Dùng cho việc cắm mốc kiểu callback (như nút TradingAgents) — start/end nằm ở hai lần
    callback khác nhau và có thể chạy trên luồng worker, không dùng cú pháp with được.
    Truyền kết quả của ``capture_context()`` làm parent để móc vào dưới root span.
    """
    if not _enabled or _tracer is None:
        return None
    try:
        span = _tracer.start_span(name, context=parent_context)
        if attributes:
            for k, v in attributes.items():
                try:
                    span.set_attribute(k, v)
                except Exception:
                    pass
        return span
    except Exception:
        return None


def set_span_attributes(span: Any, attributes: dict) -> None:
    """Bù thuộc tính cho span rời. span là None thì no-op."""
    if span is None:
        return
    for k, v in attributes.items():
        try:
            span.set_attribute(k, v)
        except Exception:
            pass


def end_span(span: Any) -> None:
    """Kết thúc một span rời. span là None thì no-op."""
    if span is None:
        return
    try:
        span.end()
    except Exception:
        pass
