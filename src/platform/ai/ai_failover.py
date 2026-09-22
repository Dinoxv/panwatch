"""AI 模型运行时 failover。

对照数据源侧成熟的降级模式(marketdata engine / kline_collector 的 `_FAIL_UNTIL`
负缓存冷却),给 AI 调用补齐"主模型失败自动切备选"的运行时能力:

- **候选模型链**:主模型 + 按优先级的备选。任一候选调用失败,按错误类别决定
  「摘参重试同模型 / 降级下一候选 / 直接抛」。
- **错误分类**(关键):
  - 参数不兼容(如某些模型不接受 temperature)→ 摘掉 temperature 重试**同一模型**一次;
  - 超时 / 5xx / 限流 / 配额 / 鉴权失效 / 服务挂 → **降级下一候选**,并把该候选记入冷却;
  - prompt / 内容策略类错误 → **不重试直接抛**(换模型也会同样失败)。
- **负缓存冷却**:照抄 `kline_collector._FAIL_UNTIL` —— 失败候选进冷却窗口,窗口内
  直接跳过不再联网;窗口过期后自然再次尝试即"恢复探测"。
- **可观测**:实际使用的模型记在 `used_model_label`(供 agent_runs 落库);发生切换时
  打 warning 日志(带 trace_id)。

`FailoverAIClient` 对外暴露与 `AIClient` 相同的 `chat / chat_multi / chat_with_tools /
chat_stream` 方法签名,可原地替换单一 client;并透传 `base_url / api_key / model /
total_tokens_used` 等属性,兼容 TradingAgents 等需要底层配置的调用方。
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)

from src.platform.ai.ai_client import AIClient
from src.platform.observability.log_context import get_log_context

logger = logging.getLogger(__name__)

# ── Phân loại lỗi ──────────────────────────────────────────────────────
ERR_PARAM = "param"    # Tham số không tương thích: bỏ tham số rồi thử lại chính mô hình đó
ERR_SWITCH = "switch"  # Hạ cấp được: chuyển sang ứng viên kế tiếp + ghi thời gian chờ
ERR_FATAL = "fatal"    # Không hạ cấp được: ném luôn (thuộc nhóm prompt / nội dung)

# ── Negative cache và thời gian chờ (chép nguyên mẫu của kline_collector) ──
# key = nhãn mô hình (ví dụ "智谱/glm-4-flash"); value = mốc monotonic khi hết thời gian chờ.
_AI_FAIL_UNTIL: dict[str, float] = {}
_AI_FAIL_COOLDOWN_S = 60.0


def clear_ai_failover_state() -> None:
    """清空冷却状态(测试隔离用)。"""
    _AI_FAIL_UNTIL.clear()


def _is_cooling(label: str) -> bool:
    return time.monotonic() < _AI_FAIL_UNTIL.get(label, 0.0)


def _mark_fail(label: str) -> None:
    _AI_FAIL_UNTIL[label] = time.monotonic() + _AI_FAIL_COOLDOWN_S


def _mark_ok(label: str) -> None:
    # Thành công là xóa dấu thời gian chờ (đã phục hồi).
    _AI_FAIL_UNTIL.pop(label, None)


def _looks_like_param_error(exc: Exception) -> bool:
    """判断 400/422 是否属于"参数不兼容"(可摘参重试)而非内容问题。"""
    msg = str(exc).lower()
    keywords = (
        "temperature",
        "unsupported parameter",
        "unsupported value",
        "does not support",
        "unknown parameter",
        "extra fields",
        "not supported",
    )
    return any(k in msg for k in keywords)


def classify_ai_error(exc: Exception) -> str:
    """把 AI 调用异常分流到三类:ERR_PARAM / ERR_SWITCH / ERR_FATAL。"""
    # Mạng / quá hạn / 5xx / giới hạn tốc độ / xác thực hết hiệu lực / thiếu quyền → đổi mô hình
    if isinstance(
        exc,
        (
            APITimeoutError,
            APIConnectionError,
            RateLimitError,
            InternalServerError,
            AuthenticationError,
            PermissionDeniedError,
        ),
    ):
        return ERR_SWITCH
    # 400 / 422: phân biệt "tham số không tương thích" (bỏ tham số thử lại) với "vấn đề nội dung / prompt" (ném luôn)
    if isinstance(exc, BadRequestError):
        return ERR_PARAM if _looks_like_param_error(exc) else ERR_FATAL
    # Các ngoại lệ khác có mã trạng thái HTTP: 5xx coi là hạ cấp được, 4xx coi là lỗi chí mạng
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return ERR_SWITCH if status >= 500 else ERR_FATAL
    # Ngoại lệ lạ: hạ cấp một cách thận trọng (ứng viên kế tiếp có thể thuộc nhà cung cấp khác, hoặc hết chuỗi thì ném ra)
    return ERR_SWITCH


class FailoverAIClient:
    """按候选链顺序尝试的 AI 客户端包装。

    Args:
        candidates: [(AIClient, 模型标签), ...],第 0 个为主模型。
        on_switch: 可选回调 (from_label, exc);发生降级切换时调用,
            供 chat SSE 端点把 failover 事件推给前端(可选)。
    """

    def __init__(
        self,
        candidates: list[tuple[AIClient, str]],
        on_switch: Callable[[str, Exception], None] | None = None,
    ):
        if not candidates:
            raise ValueError("FailoverAIClient 需要至少一个候选模型")
        self.candidates = candidates
        self.on_switch = on_switch
        # Nhãn mô hình thực dùng, mặc định là mô hình chính; gọi thành công thì cập nhật thành mô hình thật sự chạy được.
        self.used_model_label = candidates[0][1]

    # ── Thuộc tính chuyển tiếp (tương thích với phía gọi coi nó như AIClient thường) ──
    @property
    def _primary(self) -> AIClient:
        return self.candidates[0][0]

    @property
    def client(self):
        return self._primary.client

    @property
    def base_url(self) -> str:
        return self._primary.base_url

    @property
    def api_key(self) -> str:
        return self._primary.api_key

    @property
    def model(self) -> str:
        return self._primary.model

    @property
    def total_tokens_used(self) -> int:
        return sum(c.total_tokens_used for c, _ in self.candidates)

    @property
    def last_usage(self):
        """Expose the latest provider usage from the model that last ran."""
        for client, label in self.candidates:
            if label == self.used_model_label:
                return getattr(client, "last_usage", None)
        return None

    async def list_models(self) -> list[str]:
        return await self._primary.list_models()

    # ── Chọn ứng viên: ưu tiên cái không trong thời gian chờ; nếu tất cả đều đang chờ thì lấy ứng viên chính để dò phục hồi ──
    def _iter_candidates(self) -> list[tuple[AIClient, str]]:
        live = [(c, lbl) for c, lbl in self.candidates if not _is_cooling(lbl)]
        if live:
            return live
        # Tất cả đều còn trong cửa sổ chờ: hạ cấp trả về ứng viên chính (bỏ qua thời gian chờ) để dò phục hồi, thay vì thất bại ngay.
        return self.candidates[:1]

    def _log_switch(self, label: str, exc: Exception) -> None:
        trace_id = get_log_context().get("trace_id") or "-"
        logger.warning(
            "[%s] AI failover: 模型 %s 调用失败,降级下一候选: %s",
            trace_id,
            label,
            exc,
        )
        if self.on_switch is not None:
            try:
                self.on_switch(label, exc)
            except Exception:  # noqa: BLE001 — callback không được ảnh hưởng luồng chính
                logger.debug("on_switch 回调异常(已忽略)", exc_info=True)

    async def _run(self, method_name: str, *args, temperature, **kwargs):
        """非流式方法的通用 failover 执行器。"""
        last_exc: Exception | None = None
        for client, label in self._iter_candidates():
            method = getattr(client, method_name)
            try:
                result = await method(*args, temperature=temperature, **kwargs)
                _mark_ok(label)
                self.used_model_label = label
                return result
            except Exception as exc:  # noqa: BLE001
                kind = classify_ai_error(exc)
                if kind == ERR_PARAM:
                    # Bỏ temperature rồi thử lại chính mô hình đó một lần
                    try:
                        retry_kwargs = dict(kwargs)
                        retry_kwargs["temperature"] = None
                        # Some OpenAI-compatible services reject max_tokens;
                        # a summary can still fall back to local normalization.
                        retry_kwargs.pop("max_tokens", None)
                        result = await method(*args, **retry_kwargs)
                        _mark_ok(label)
                        self.used_model_label = label
                        return result
                    except Exception as exc2:  # noqa: BLE001
                        exc = exc2
                        kind = classify_ai_error(exc2)
                if kind == ERR_FATAL:
                    raise
                # ERR_SWITCH: ghi thời gian chờ + ghi nhật ký + thử ứng viên kế tiếp
                _mark_fail(label)
                last_exc = exc
                self._log_switch(label, exc)
                continue
        raise last_exc or RuntimeError("所有候选模型均失败")

    async def chat(
        self,
        system_prompt: str,
        user_content: str,
        images: list[str] | None = None,
        temperature: float | None = 0.4,
    ) -> str:
        return await self._run(
            "chat", system_prompt, user_content, images=images, temperature=temperature
        )

    async def chat_multi(
        self,
        messages: list[dict],
        temperature: float | None = 0.4,
        max_tokens: int | None = None,
    ) -> str:
        kwargs = {"max_tokens": max_tokens} if max_tokens is not None else {}
        return await self._run(
            "chat_multi", messages, temperature=temperature, **kwargs
        )

    async def chat_with_tools(
        self, messages: list[dict], tools: list[dict], temperature: float | None = 0.4
    ):
        return await self._run(
            "chat_with_tools", messages, tools, temperature=temperature
        )

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = 0.4,
        tool_choice: str | None = None,
    ):
        """流式 failover。

        注意:一旦已经产出过 token,再失败无法回滚(已推给前端),故 failover
        只能安全覆盖"首个 token 之前"的失败;首包后异常直接透出。
        """
        last_exc: Exception | None = None
        for client, label in self._iter_candidates():
            started = False
            try:
                stream_kwargs = {"tools": tools, "temperature": temperature}
                if tool_choice is not None:
                    stream_kwargs["tool_choice"] = tool_choice
                async for ev in client.chat_stream(messages, **stream_kwargs):
                    started = True
                    yield ev
                _mark_ok(label)
                self.used_model_label = label
                return
            except Exception as exc:  # noqa: BLE001
                if started:
                    raise
                kind = classify_ai_error(exc)
                if kind == ERR_PARAM:
                    try:
                        retry_kwargs = {"tools": tools, "temperature": None}
                        if tool_choice is not None:
                            retry_kwargs["tool_choice"] = tool_choice
                        async for ev in client.chat_stream(messages, **retry_kwargs):
                            started = True
                            yield ev
                        _mark_ok(label)
                        self.used_model_label = label
                        return
                    except Exception as exc2:  # noqa: BLE001
                        if started:
                            raise
                        exc = exc2
                        kind = classify_ai_error(exc2)
                if kind == ERR_FATAL:
                    raise
                _mark_fail(label)
                last_exc = exc
                self._log_switch(label, exc)
                continue
        raise last_exc or RuntimeError("所有候选模型均失败(流式)")


def _make_client(base_url: str, api_key: str, model: str, proxy: str) -> AIClient:
    return AIClient(base_url=base_url, api_key=api_key, model=model, proxy=proxy)


def build_failover_client(
    primary_model,
    primary_service,
    proxy: str = "",
    *,
    db=None,
    settings=None,
    max_fallbacks: int = 3,
) -> FailoverAIClient:
    """根据主模型 + 库里其余模型构建候选链。

    Args:
        primary_model / primary_service: 上层四级/三级路由已选定的主模型(可为 detached
            ORM 对象;仅读字段,不触发 lazy load)。二者任一为空时用环境变量作主候选。
        proxy: HTTP 代理。
        db: 可选的 Session;传入则复用,否则内部开一个只读会话查备选模型。
        settings: 可选的 Settings(环境变量兜底用)。
        max_fallbacks: 主模型之外最多挂几个备选。

    与四级路由自洽:主候选沿用上层已解析结果,备选按 `is_default` 优先、其余按 id
    顺序补齐,天然复用现有 AIService/AIModel 配置体系,无需新增全局配置。
    """
    from src.platform.runtime.config import Settings

    candidates: list[tuple[AIClient, str]] = []
    primary_model_id = None

    if primary_model and primary_service:
        candidates.append(
            (
                _make_client(
                    primary_service.base_url,
                    primary_service.api_key,
                    primary_model.model,
                    proxy,
                ),
                f"{primary_service.name}/{primary_model.model}",
            )
        )
        primary_model_id = getattr(primary_model, "id", None)
    else:
        s = settings or Settings()
        candidates.append(
            (
                _make_client(s.ai_base_url, s.ai_api_key, s.ai_model, proxy),
                f"env/{s.ai_model}",
            )
        )

    # Bổ sung đủ các ứng viên dự phòng
    own_session = False
    if db is None:
        from src.platform.persistence.database import SessionLocal

        db = SessionLocal()
        own_session = True
    try:
        from src.platform.persistence.models import AIModel, AIService

        rows = (
            db.query(AIModel)
            .order_by(AIModel.is_default.desc(), AIModel.id.asc())
            .all()
        )
        for m in rows:
            if len(candidates) >= max_fallbacks + 1:
                break
            if primary_model_id is not None and m.id == primary_model_id:
                continue
            svc = db.query(AIService).filter(AIService.id == m.service_id).first()
            if not svc:
                continue
            candidates.append(
                (
                    _make_client(svc.base_url, svc.api_key, m.model, proxy),
                    f"{svc.name}/{m.model}",
                )
            )
    except Exception:  # noqa: BLE001 — truy vấn ứng viên dự phòng lỗi cũng không ảnh hưởng ứng viên chính
        logger.warning("构建 failover 备选候选失败,仅用主模型", exc_info=True)
    finally:
        if own_session:
            db.close()

    return FailoverAIClient(candidates)


def get_configured_failover_client(db, model_id: int | None = None) -> FailoverAIClient:
    """Select the requested/default persisted model and build its failover chain.

    HTTP routers in several business modules need an AI client.  Model selection is
    infrastructure composition, so callers use this platform function instead of
    borrowing a helper from the assistant's HTTP router.
    """
    from src.platform.persistence.models import AIModel, AIService

    model = None
    if model_id:
        model = db.query(AIModel).filter(AIModel.id == model_id).first()
    if not model:
        model = db.query(AIModel).filter(AIModel.is_default == True).first()  # noqa: E712
    if not model:
        model = db.query(AIModel).first()
    service = db.query(AIService).filter(AIService.id == model.service_id).first() if model else None
    return build_failover_client(model, service, db=db)
