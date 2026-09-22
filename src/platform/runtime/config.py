"""从环境和项目配置文件读取运行期设置的技术边界。

该模块可同时被 HTTP、后台任务和平台适配器使用；它不包含任何投资或产品决策。
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings

from src.platform.marketdata.models import MarketCode


class Settings(BaseSettings):
    """环境变量配置"""

    # AI
    ai_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    ai_api_key: str = ""
    ai_model: str = "glm-4"

    # Assistant context engineering. The compression model is optional: when
    # unset, the host reuses the configured default assistant model.
    context_compression_model_id: int | None = Field(default=None, ge=1)
    context_compression_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    context_summary_max_tokens: int = Field(default=800, ge=128, le=4_000)
    context_max_tokens: int = Field(default=12_000, ge=256)
    context_soft_limit_tokens: int = Field(default=8_400, ge=128)
    context_hard_limit_tokens: int = Field(default=10_200, ge=256)
    context_keep_recent_messages: int = Field(default=8, ge=1, le=100)
    tool_research_enabled: bool = True

    # Telegram
    notify_telegram_bot_token: str = ""
    notify_telegram_chat_id: str = ""

    # Proxy
    http_proxy: str = ""

    # Chính sách thông báo (có thể ghi đè ở mục “cài đặt hệ thống” trên giao diện)
    # Khung giờ im lặng (múi giờ địa phương), định dạng HH:MM-HH:MM, để trống là tắt; ví dụ qua đêm: 23:00-07:00
    notify_quiet_hours: str = ""
    # Số lần thử lại khi gửi thông báo thất bại (không tính lần đầu)
    notify_retry_attempts: int = 2
    # Số giây giãn cách khi thử lại (giá trị cơ sở), thực tế tăng dần theo 1x, 2x, ...
    notify_retry_backoff_seconds: float = 2.0
    # Ghi đè cửa sổ chống lặp (JSON), ví dụ: {"news_digest":60,"daily_report":720}
    notify_dedupe_ttl_overrides: str = ""

    # Chứng chỉ SSL (môi trường doanh nghiệp)
    ca_cert_file: str = ""

    # Lập lịch
    # day_of_week dùng ngữ nghĩa POSIX cron (1-5 = thứ Hai đến thứ Sáu)
    daily_report_cron: str = "30 15 * * 1-5"

    # Múi giờ mặc định (dùng cho lập lịch, hiển thị thời gian...).
    # Thống nhất điều khiển bằng một biến môi trường duy nhất: TZ (mặc định Asia/Shanghai).
    # Nên dùng tên múi giờ IANA, ví dụ Asia/Shanghai, America/New_York.
    app_timezone: str = Field(
        default="Asia/Shanghai",
        validation_alias=AliasChoices("TZ", "APP_TIMEZONE"),
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        # Trong .env có thể có các trường chưa khai báo như HTTPS_PROXY (biến chuẩn của httpx / hệ thống), bỏ qua chứ không báo lỗi
        "extra": "ignore",
    }

    @model_validator(mode="after")
    def validate_context_thresholds(self) -> "Settings":
        if not self.context_soft_limit_tokens < self.context_hard_limit_tokens <= self.context_max_tokens:
            raise ValueError(
                "context thresholds must satisfy soft_limit < hard_limit <= max_tokens"
            )
        return self


@dataclass
class StockConfig:
    """自选股配置"""

    symbol: str
    name: str
    market: MarketCode


@dataclass
class AppConfig:
    """应用完整配置"""

    settings: Settings
    watchlist: list[StockConfig] = field(default_factory=list)


def load_watchlist(path: str | Path = "config/watchlist.yaml") -> list[StockConfig]:
    """从 YAML 加载自选股列表"""
    path = Path(path)
    if not path.exists():
        return []

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    stocks = []
    for market_group in data.get("markets", []):
        market_code = MarketCode(market_group["code"])
        for stock in market_group.get("stocks", []):
            stocks.append(
                StockConfig(
                    symbol=stock["symbol"],
                    name=stock["name"],
                    market=market_code,
                )
            )

    return stocks


def load_config() -> AppConfig:
    """加载完整配置"""
    settings = Settings()
    watchlist = load_watchlist()
    return AppConfig(settings=settings, watchlist=watchlist)
