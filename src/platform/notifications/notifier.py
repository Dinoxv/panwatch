import logging
import os
import re

import apprise
import asyncio
import httpx

logger = logging.getLogger(__name__)


def get_global_proxy() -> str:
    """Lấy thiết lập proxy HTTP toàn cục"""
    try:
        from src.platform.persistence.database import SessionLocal
        from src.platform.persistence.models import AppSettings

        db = SessionLocal()
        try:
            setting = (
                db.query(AppSettings).filter(AppSettings.key == "http_proxy").first()
            )
            return setting.value if setting and setting.value else ""
        finally:
            db.close()
    except Exception:
        return ""


def sanitize_for_telegram(content: str) -> str:
    """Làm sạch nội dung cho hợp Telegram (bỏ định dạng HTML và Markdown)"""
    # Bỏ thẻ HTML
    content = re.sub(r"</?table[^>]*>", "", content)
    content = re.sub(r"</?thead[^>]*>", "", content)
    content = re.sub(r"</?tbody[^>]*>", "", content)
    content = re.sub(r"</?tr[^>]*>", "\n", content)
    content = re.sub(r"</?th[^>]*>", " | ", content)
    content = re.sub(r"</?td[^>]*>", " | ", content)
    content = re.sub(r"</?div[^>]*>", "", content)
    content = re.sub(r"</?span[^>]*>", "", content)
    content = re.sub(r"</?p[^>]*>", "\n", content)
    content = re.sub(r"<br\s*/?>", "\n", content)

    # Bỏ định dạng Markdown
    # Liên kết markdown [label](url) → "label url": liên kết nội tuyến của Telegram không dựng được với localhost / IP:cổng
    # và các địa chỉ không thuộc mạng công cộng (nhãn tụt thành văn bản thuần, bấm không được), còn URL trần thì được tự nhận là bấm được nên ổn định hơn.
    content = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1 \2", content)
    content = re.sub(r"^#{1,6}\s*", "", content, flags=re.MULTILINE)  # Bỏ dấu # của tiêu đề
    content = re.sub(r"\*\*(.+?)\*\*", r"\1", content)  # Bỏ dấu ** của chữ đậm
    content = re.sub(r"\*(.+?)\*", r"\1", content)  # Bỏ dấu * của chữ nghiêng
    content = re.sub(r"__(.+?)__", r"\1", content)  # Bỏ dấu __ của chữ đậm
    content = re.sub(r"_(.+?)_", r"\1", content)  # Bỏ dấu _ của chữ nghiêng
    content = re.sub(r"~~(.+?)~~", r"\1", content)  # Bỏ gạch ngang chữ
    content = re.sub(r"`(.+?)`", r"\1", content)  # Bỏ mã nội dòng
    content = re.sub(
        r"^\s*[-*+]\s+", "· ", content, flags=re.MULTILINE
    )  # Đổi ký hiệu danh sách thành ·
    content = re.sub(
        r"^\s*\d+\.\s+", "", content, flags=re.MULTILINE
    )  # Bỏ số thứ tự của danh sách đánh số

    # Dọn khoảng trắng thừa
    content = re.sub(r"\n\s*\n\s*\n", "\n\n", content)
    content = re.sub(r" +", " ", content)
    return content.strip()


# Định nghĩa các loại kênh (nhãn + các trường biểu mẫu)
CHANNEL_TYPES = {
    "telegram": {
        "label": "Telegram",
        "fields": ["bot_token", "chat_id", "proxy"],
    },
    "bark": {
        "label": "Bark",
        "fields": ["device_key", "server_url"],
    },
    "dingtalk": {
        "label": "钉钉机器人",
        "fields": [
            "token",
            "secret",
            "phones",
            "keyword",
        ],  # keyword không bắt buộc: khi thiết lập bảo mật là “từ khóa” thì tự động nối thêm
    },
    "wecom": {
        "label": "企业微信机器人",
        "fields": ["webhook_key"],
    },
    "lark": {
        "label": "飞书机器人",
        "fields": ["webhook_token"],
    },
    "serverchan": {
        "label": "Server酱",
        "fields": ["sendkey"],
    },
    "pushplus": {
        "label": "PushPlus",
        "fields": ["token", "topic"],
    },
    "discord": {
        "label": "Discord",
        "fields": ["webhook_id", "webhook_token"],
    },
    "pushover": {
        "label": "Pushover",
        "fields": ["user_key", "app_token"],
    },
}

# Các loại kênh do Apprise hỗ trợ (khi không cấu hình proxy)
_APPRISE_TYPES = {"telegram", "bark", "dingtalk", "lark", "discord", "pushover"}

# Các loại kênh tự cài đặt (có proxy hoặc yêu cầu đặc biệt)
_CUSTOM_IMPL_TYPES = {"wecom", "serverchan", "pushplus"}

# Kênh hỗ trợ Markdown (không cần làm sạch)
_MARKDOWN_CHANNELS = {"wecom", "serverchan", "pushplus", "dingtalk", "lark", "discord"}

# Kênh không hỗ trợ Markdown (phải làm sạch)
_PLAIN_TEXT_CHANNELS = {"telegram", "bark", "pushover"}


def build_apprise_url(channel_type: str, config: dict) -> str | None:
    """
    Dựng URL Apprise theo loại kênh và cấu hình

    Returns:
        URL Apprise hoặc None (nếu cần gửi theo cách riêng, như Telegram có proxy)
    """
    if channel_type == "telegram":
        bot_token = config.get("bot_token", "")
        chat_id = config.get("chat_id", "")
        if not bot_token or not chat_id:
            raise ValueError("Telegram 需要 bot_token 和 chat_id")
        # Nếu đã cấu hình proxy (cấp kênh hoặc toàn cục) thì trả None và gửi bằng cách tự cài đặt
        proxy = config.get("proxy", "").strip() or get_global_proxy()
        if proxy:
            return None
        return f"tgram://{bot_token}/{chat_id}"

    elif channel_type == "bark":
        device_key = config.get("device_key", "")
        server_url = config.get("server_url", "").strip("/")
        if not device_key:
            raise ValueError("Bark 需要 device_key")
        if server_url:
            host = server_url.replace("https://", "").replace("http://", "")
            return f"bark://{host}/{device_key}/"
        return f"bark://{device_key}/"

    elif channel_type == "dingtalk":
        # Định dạng DingTalk của Apprise:
        # - Không ký: dingtalk://{access_token}/
        # - Có ký:  dingtalk://{secret}@{access_token}/
        # - Nhắc theo số điện thoại: nối vào cuối URL ?to=13800138000,13900139000
        token = (config.get("token") or "").strip()
        secret = (config.get("secret") or "").strip()
        phones = (config.get("phones") or "").strip()
        if not token:
            raise ValueError("钉钉需要 token")
        base = f"dingtalk://{secret}@{token}/" if secret else f"dingtalk://{token}/"
        if phones:
            # Chỉ giữ chữ số và dấu phẩy
            phone_list = [
                re.sub(r"[^0-9]", "", p)
                for p in phones.split(",")
                if re.sub(r"[^0-9]", "", p)
            ]
            if phone_list:
                base += f"?to={','.join(phone_list)}"
        return base

    elif channel_type == "lark":
        webhook_token = config.get("webhook_token", "")
        if not webhook_token:
            raise ValueError("飞书需要 webhook_token")
        return f"lark://{webhook_token}/"

    elif channel_type == "discord":
        webhook_id = config.get("webhook_id", "")
        webhook_token = config.get("webhook_token", "")
        if not webhook_id or not webhook_token:
            raise ValueError("Discord 需要 webhook_id 和 webhook_token")
        return f"discord://{webhook_id}/{webhook_token}/"

    elif channel_type == "pushover":
        user_key = config.get("user_key", "")
        app_token = config.get("app_token", "")
        if not user_key or not app_token:
            raise ValueError("Pushover 需要 user_key 和 app_token")
        return f"pover://{user_key}@{app_token}/"

    else:
        raise ValueError(f"不支持的 Apprise 渠道类型: {channel_type}")


class NotifierManager:
    """Bộ quản lý thông báo: kênh Apprise + kênh tự viết"""

    def __init__(self, policy=None):
        self._ap = apprise.Apprise()
        self._custom_channels: list[tuple[str, dict]] = []
        self._channel_count = 0
        # Từ khóa DingTalk (tùy chọn): nếu bot nhóm bật kiểm tra bảo mật “từ khóa” thì tự động nối thêm
        self._dingtalk_keywords: set[str] = set()
        self.policy = policy

    def add_channel(self, channel_type: str, config: dict):
        """Thêm kênh thông báo"""
        try:
            if channel_type in _APPRISE_TYPES:
                url = build_apprise_url(channel_type, config)
                if url is None:
                    # Cần tự cài đặt (ví dụ Telegram đi qua proxy)
                    self._custom_channels.append((channel_type, config))
                    self._channel_count += 1
                    logger.info(f"注册自定义通知渠道: {channel_type} (带代理)")
                elif self._ap.add(url):
                    self._channel_count += 1
                    logger.info(f"注册通知渠道: {channel_type}")
                else:
                    logger.error(f"注册通知渠道失败: {channel_type} (URL 无效)")
                if channel_type == "dingtalk":
                    kw = (config.get("keyword") or "").strip()
                    if kw:
                        self._dingtalk_keywords.add(kw)
            else:
                self._custom_channels.append((channel_type, config))
                self._channel_count += 1
                logger.info(f"注册自定义通知渠道: {channel_type}")
        except ValueError as e:
            logger.error(f"注册通知渠道失败: {e}")

    async def notify(self, title: str, content: str, images: list[str] | None = None):
        """Gửi thông báo tới mọi kênh đã đăng ký (bỏ qua lỗi)"""
        await self.notify_with_result(title, content, images)

    async def notify_with_result(
        self,
        title: str,
        content: str,
        images: list[str] | None = None,
        *,
        bypass_quiet_hours: bool = False,
    ) -> dict:
        """Gửi thông báo tới mọi kênh đã đăng ký, trả về kết quả"""
        if self._channel_count == 0:
            logger.warning("没有可用的通知渠道")
            return {"success": False, "error": "没有可用的通知渠道"}

        # Quiet hours
        try:
            if not bypass_quiet_hours and getattr(self, "policy", None):
                if self.policy.is_quiet_now():
                    logger.info("当前处于通知静默时段，跳过发送")
                    return {"success": False, "skipped": "quiet_hours"}
        except Exception:
            # do not block sends on policy errors
            pass

        # Chuẩn bị bản văn bản thuần (cho các kênh không hỗ trợ Markdown)
        plain_content = sanitize_for_telegram(content)

        # Chuẩn bị tệp đính kèm
        attachments = None
        if images:
            attachments = apprise.AppriseAttachment()
            for img_path in images:
                if img_path and os.path.exists(img_path):
                    attachments.add(img_path)

        errors = []

        # Nếu đã cấu hình từ khóa DingTalk thì tự nối vào cuối nội dung để qua được kiểm tra “từ khóa”
        if self._dingtalk_keywords:
            suffix = " " + " ".join(sorted(self._dingtalk_keywords))
            if suffix.strip() not in plain_content:
                plain_content = (plain_content + "\n" + suffix).strip()
            if suffix.strip() not in content:
                content = (content + "\n" + suffix).strip()

        retry_attempts = 0
        backoff = 0.0
        try:
            if getattr(self, "policy", None):
                retry_attempts = max(0, int(self.policy.retry_attempts))
                backoff = float(self.policy.retry_backoff_seconds or 0.0)
        except Exception:
            retry_attempts = 0
            backoff = 0.0

        async def _sleep_retry(i: int):
            if backoff <= 0:
                return
            await asyncio.sleep(backoff * (2 ** max(0, i - 1)))

        # Kênh qua Apprise (dùng văn bản thuần, vì Telegram và vài kênh khác không hỗ trợ Markdown)
        if len(self._ap) > 0:
            apprise_ok = False
            last_err = ""
            for attempt in range(0, retry_attempts + 1):
                try:
                    success = await self._ap.async_notify(
                        title=title,
                        body=plain_content,
                        body_format=apprise.NotifyFormat.TEXT,
                        attach=attachments,
                    )
                    if success:
                        apprise_ok = True
                        logger.info(f"Apprise 通知发送成功: {title}")
                        break
                    last_err = "Apprise 通知发送失败（可能是网络问题或配置错误）"
                    logger.error(f"{last_err}: {title}")
                except Exception as e:
                    last_err = f"Apprise 通知异常: {e}"
                    logger.error(last_err)
                if attempt < retry_attempts:
                    await _sleep_retry(attempt + 1)
            if not apprise_ok:
                errors.append(last_err or "Apprise 通知发送失败")

        # Kênh tự cài đặt (tự chọn định dạng theo loại kênh)
        for ch_type, config in self._custom_channels:
            ch_ok = False
            last_err = ""
            for attempt in range(0, retry_attempts + 1):
                try:
                    # Kênh hỗ trợ Markdown thì dùng nội dung gốc, còn lại dùng văn bản thuần
                    ch_content = (
                        content if ch_type in _MARKDOWN_CHANNELS else plain_content
                    )
                    await self._send_custom(ch_type, config, title, ch_content)
                    ch_ok = True
                    break
                except Exception as e:
                    last_err = f"{ch_type} 发送失败: {e}"
                    logger.error(last_err)
                if attempt < retry_attempts:
                    await _sleep_retry(attempt + 1)
            if not ch_ok:
                errors.append(last_err or f"{ch_type} 发送失败")

        if errors:
            return {"success": False, "error": "; ".join(errors)}
        return {"success": True}

    async def _send_custom(self, ch_type: str, config: dict, title: str, content: str):
        """Gửi thông báo qua kênh tự viết"""
        if ch_type == "telegram":
            await self._send_telegram(config, title, content)
        elif ch_type == "wecom":
            await self._send_wecom(config, title, content)
        elif ch_type == "serverchan":
            await self._send_serverchan(config, title, content)
        elif ch_type == "pushplus":
            await self._send_pushplus(config, title, content)
        else:
            logger.warning(f"未知的自定义渠道类型: {ch_type}")

    async def _send_telegram(self, config: dict, title: str, content: str):
        """Telegram Bot API (hỗ trợ proxy)

        Bộ đọc Markdown cũ của Telegram rất mong manh:
        - Không nhận `**đậm**` (chỉ nhận `*đậm*`), kiểu GitHub sẽ gây lỗi Can't find end of entity
        - Không nhận `### tiêu đề` (coi # là ký tự thường, nhưng phần sau ### có thể bị cắt)
        - Mỗi tin tối đa 4096 ký tự, vượt thì bị cắt làm hỏng thực thể
        Nên tiền xử lý cho tương thích + cắt bớt trước khi gửi.
        """
        bot_token = config.get("bot_token", "")
        chat_id = config.get("chat_id", "")
        # Ưu tiên proxy cấp kênh, không có thì dùng proxy toàn cục
        proxy = config.get("proxy", "").strip() or get_global_proxy()

        if not bot_token or not chat_id:
            raise ValueError("Telegram 需要 bot_token 和 chat_id")

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        # Dùng sẵn sanitize_for_telegram để lột sạch markdown thành văn bản thuần,
        # tránh việc `**chữ đậm**` / `## tiêu đề` / thực thể chưa đóng làm Telegram phân tích thất bại.
        # Bọc tiêu đề bằng `*...*` thủ công cho đậm (Markdown đời cũ của Telegram chỉ nhận một dấu sao).
        safe_title = sanitize_for_telegram(title) if title else ""
        safe_content = sanitize_for_telegram(content)
        text = f"*{safe_title}*\n\n{safe_content}" if safe_title else safe_content
        # Telegram giới hạn 4096 ký tự mỗi tin, chừa một khoảng đệm cho dòng nhắc ở cuối
        if len(text) > 3900:
            # Nếu cuối phần thân có liên kết chi tiết (sau khi làm sạch đã thành URL trần) thì cắt thẳng sẽ chém mất nó →
            # người dùng không bấm được. Vậy nên tách nó ra trước, cắt phần thân xong mới ghép lại vào cuối.
            link_m = re.search(r"(https?://[^\s)]+)\s*$", text)
            if link_m:
                notice = f"\n\n…内容过长已截断,完整报告 👉 {link_m.group(1)}"
            else:
                notice = "\n\n…内容过长已截断,完整报告请在 PanWatch 查看"
            text = text[: 3900 - len(notice)].rstrip() + notice
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }

        # Cấu hình proxy
        transport = None
        if proxy:
            transport = httpx.AsyncHTTPTransport(proxy=proxy)
            logger.debug(f"Telegram 使用代理: {proxy}")

        try:
            async with httpx.AsyncClient(transport=transport, timeout=30) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if not data.get("ok"):
                    raise RuntimeError(f"Telegram API 错误: {data.get('description')}")
                logger.info(f"Telegram 通知发送成功: {title}")
        except httpx.ConnectError as e:
            if proxy:
                raise RuntimeError(f"连接代理失败 ({proxy}): {e}")
            else:
                raise RuntimeError(f"无法连接 Telegram API（可能需要配置代理）: {e}")
        except httpx.TimeoutException:
            raise RuntimeError("请求超时（网络问题或代理配置错误）")
        except Exception as e:
            if (
                "ConnectError" in str(type(e).__name__)
                or "connection" in str(e).lower()
            ):
                if not proxy:
                    raise RuntimeError(f"网络连接失败，建议配置代理: {e}")
            raise

    async def _send_wecom(self, config: dict, title: str, content: str):
        """Webhook bot WeCom"""
        key = config.get("webhook_key", "")
        if not key:
            raise ValueError("企业微信需要 webhook_key")

        url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"
        text = f"## {title}\n\n{content}" if title else content
        payload = {"msgtype": "markdown", "markdown": {"content": text}}

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=30)
            data = resp.json()
            if data.get("errcode") != 0:
                raise RuntimeError(f"企业微信发送失败: {data.get('errmsg')}")
            logger.info(f"企业微信通知发送成功: {title}")

    async def _send_serverchan(self, config: dict, title: str, content: str):
        """Đẩy tin qua ServerChan"""
        sendkey = config.get("sendkey", "")
        if not sendkey:
            raise ValueError("Server酱需要 sendkey")

        url = f"https://sctapi.ftqq.com/{sendkey}.send"
        payload = {"title": title or "通知", "desp": content}

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=30)
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Server酱发送失败: {data.get('message')}")
            logger.info(f"Server酱通知发送成功: {title}")

    async def _send_pushplus(self, config: dict, title: str, content: str):
        """Đẩy tin qua PushPlus"""
        token = config.get("token", "")
        if not token:
            raise ValueError("PushPlus 需要 token")

        url = "https://www.pushplus.plus/send"
        payload = {
            "token": token,
            "title": title or "通知",
            "content": content,
            "template": "markdown",
        }
        topic = config.get("topic", "")
        if topic:
            payload["topic"] = topic

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=30)
            data = resp.json()
            if data.get("code") != 200:
                raise RuntimeError(f"PushPlus 发送失败: {data.get('msg')}")
            logger.info(f"PushPlus 通知发送成功: {title}")
