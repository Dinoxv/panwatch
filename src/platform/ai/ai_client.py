import base64
import logging
from pathlib import Path

from openai import AsyncOpenAI
from pan_agent_token_meter import normalize_provider_usage

from src.platform.observability import otel

logger = logging.getLogger(__name__)


class AIClient:
    """Máy khách AI tương thích giao thức OpenAI"""

    def __init__(self, base_url: str, api_key: str, model: str = "", proxy: str = ""):
        kwargs = {
            "base_url": base_url,
            "api_key": api_key,
        }
        if proxy:
            kwargs["http_client"] = None  # TODO: cần proxy thì cấu hình qua httpx
        self.client = AsyncOpenAI(**kwargs)
        # Giữ cấu hình gốc làm thuộc tính của thực thể, cho các agent cần bắc cầu sang framework LLM bên thứ ba dùng
        # (ví dụ TradingAgents cần base_url + api_key để dựng lại LLM của langchain)
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.total_tokens_used = 0
        self.last_usage = None

    async def chat(
        self,
        system_prompt: str,
        user_content: str,
        images: list[str] | None = None,
        temperature: float | None = 0.4,
    ) -> str:
        """
        Gọi LLM lấy câu trả lời dạng văn bản.

        Args:
            system_prompt: prompt hệ thống
            user_content: nội dung người dùng nhập
            images: danh sách đường dẫn ảnh (cho đa phương thức, tùy chọn)
            temperature: nhiệt độ sinh
        """
        messages = [
            {"role": "system", "content": system_prompt},
        ]

        # Dựng user message
        if images:
            content_parts = [{"type": "text", "text": user_content}]
            for img_path in images:
                img_data = self._encode_image(img_path)
                if img_data:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img_data}"}
                    })
            messages.append({"role": "user", "content": content_parts})
        else:
            messages.append({"role": "user", "content": user_content})

        try:
            create_kwargs = {"model": self.model, "messages": messages}
            if temperature is not None:
                create_kwargs["temperature"] = temperature
            # Span gen_ai của OTel (no-op khi tắt mặc định); lượng token điền ngược sau khi nhận được usage.
            with otel.llm_span(self.model, operation="chat") as _span:
                response = await self.client.chat.completions.create(**create_kwargs)
                # Ghi nhận lượng token
                if response.usage:
                    self.last_usage = normalize_provider_usage(response.usage, model=self.model)
                    self.total_tokens_used += response.usage.total_tokens
                    _span.set_response(
                        model=getattr(response, "model", None) or self.model,
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                    )
                    logger.debug(
                        f"Token usage: {response.usage.prompt_tokens} + "
                        f"{response.usage.completion_tokens} = {response.usage.total_tokens}"
                    )

            return response.choices[0].message.content or ""

        except Exception as e:
            logger.error(f"AI 调用失败: {e}")
            raise

    async def chat_multi(
        self,
        messages: list[dict],
        temperature: float | None = 0.4,
        max_tokens: int | None = None,
    ) -> str:
        """
        Phiên nhiều lượt: truyền vào danh sách messages đầy đủ.

        Args:
            messages: [{"role": "system"/"user"/"assistant", "content": "..."}]
            temperature: nhiệt độ sinh; truyền None thì không gửi tham số này
                (dùng cho việc failover bỏ tham số rồi thử lại khi gặp lỗi "tham số không tương thích")
        """
        try:
            create_kwargs: dict = {"model": self.model, "messages": messages}
            if temperature is not None:
                create_kwargs["temperature"] = temperature
            if max_tokens is not None:
                create_kwargs["max_tokens"] = max_tokens
            with otel.llm_span(self.model, operation="chat") as _span:
                response = await self.client.chat.completions.create(**create_kwargs)
                if response.usage:
                    self.last_usage = normalize_provider_usage(response.usage, model=self.model)
                    self.total_tokens_used += response.usage.total_tokens
                    _span.set_response(
                        model=getattr(response, "model", None) or self.model,
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                    )
                    logger.debug(
                        f"Token usage: {response.usage.prompt_tokens} + "
                        f"{response.usage.completion_tokens} = {response.usage.total_tokens}"
                    )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"AI 多轮对话调用失败: {e}")
            raise

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float | None = 0.4,
    ):
        """Lời gọi phiên có tool use, trả về đối tượng message gốc.

        temperature truyền None thì không gửi tham số này (cho failover bỏ tham số rồi thử lại).
        """
        try:
            create_kwargs: dict = {
                "model": self.model,
                "messages": messages,
                "tools": tools,
            }
            if temperature is not None:
                create_kwargs["temperature"] = temperature
            with otel.llm_span(self.model, operation="chat") as _span:
                response = await self.client.chat.completions.create(**create_kwargs)
                if response.usage:
                    self.last_usage = normalize_provider_usage(response.usage, model=self.model)
                    self.total_tokens_used += response.usage.total_tokens
                    _span.set_response(
                        model=getattr(response, "model", None) or self.model,
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                    )
            return response.choices[0].message
        except Exception as e:
            logger.error(f"AI tool use 调用失败: {e}")
            raise

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = 0.4,
        tool_choice: str | None = None,
    ):
        """Kênh phiên theo luồng (stream=True), hỗ trợ tool use tùy chọn.

        Là generator bất đồng bộ, sinh ra các sự kiện dạng bộ đôi:
        - ("token", str): mẩu văn bản tăng thêm, vừa sinh vừa đẩy ra;
        - ("message", dict): sau khi luồng kết thúc thì sinh một lần thông điệp đầy đủ,
          dạng {"content": toàn văn, "tool_calls": [{"id", "name", "arguments"}, ...]},
          không gọi công cụ thì tool_calls là danh sách rỗng.

        Bên gọi (như điểm cuối SSE của chat) dựa vào tool_calls rỗng hay không để quyết
        định chạy tiếp vòng lặp công cụ hay kết thúc.
        """
        create_kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if temperature is not None:
            create_kwargs["temperature"] = temperature
        if tools:
            create_kwargs["tools"] = tools
        if tool_choice is not None:
            create_kwargs["tool_choice"] = tool_choice
        # OpenAI-compatible providers that support streaming usage return a
        # final usage-only chunk. Providers that reject this optional field
        # are retried without it below.
        create_kwargs["stream_options"] = {"include_usage": True}

        try:
            stream = await self.client.chat.completions.create(**create_kwargs)
        except Exception as e:
            message = str(e).lower()
            unsupported_stream_options = any(
                marker in message
                for marker in ("stream_options", "unsupported parameter", "unknown parameter")
            )
            if "stream_options" in create_kwargs and unsupported_stream_options:
                create_kwargs.pop("stream_options")
                try:
                    stream = await self.client.chat.completions.create(**create_kwargs)
                except Exception:
                    logger.error(f"AI 流式调用失败: {e}")
                    raise
            else:
                logger.error(f"AI 流式调用失败: {e}")
                raise

        content_parts: list[str] = []
        # Theo giao thức luồng của OpenAI, tool_calls được gửi thành từng mảnh theo index (arguments ghép dần từng đoạn)
        tool_calls_acc: dict[int, dict] = {}
        provider_usage = None

        async for chunk in stream:
            # Một số dịch vụ tương thích gửi riêng ở cuối một chunk chỉ chứa usage
            usage = getattr(chunk, "usage", None)
            if usage:
                self.total_tokens_used += usage.total_tokens
                provider_usage = normalize_provider_usage(usage, model=self.model)
                self.last_usage = provider_usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            if delta.content:
                content_parts.append(delta.content)
                yield ("token", delta.content)
            for tc in delta.tool_calls or []:
                acc = tool_calls_acc.setdefault(
                    tc.index, {"id": "", "name": "", "arguments": ""}
                )
                if tc.id:
                    acc["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        acc["name"] = tc.function.name
                    if tc.function.arguments:
                        acc["arguments"] += tc.function.arguments

        yield (
            "message",
            {
                "content": "".join(content_parts),
                "tool_calls": [tool_calls_acc[i] for i in sorted(tool_calls_acc)],
                "usage": provider_usage.model_dump(mode="json") if provider_usage else None,
            },
        )

    async def list_models(self) -> list[str]:
        """Kéo danh sách id mô hình dùng được qua /v1/models tương thích OpenAI."""
        resp = await self.client.models.list()
        return sorted(m.id for m in resp.data)

    def _encode_image(self, image_path: str) -> str | None:
        """Mã hóa tệp ảnh thành base64"""
        path = Path(image_path)
        if not path.exists():
            logger.warning(f"图片不存在: {image_path}")
            return None
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
