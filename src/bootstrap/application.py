"""Gốc lắp ráp ứng dụng ASGI của PanWatch.

Đây là chỗ duy nhất tạo thực thể :class:`fastapi.FastAPI` lúc tiến trình khởi động. Nó chỉ
nối middleware HTTP, phụ thuộc xác thực và router của từng module; quy tắc nghiệp vụ cụ
thể vẫn do ``modules`` và ``platform`` gánh, tránh để lối vào ứng dụng biến thành một tầng
nghiệp vụ dùng chung mới.
"""

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from src.modules.administration.api import (
    auth,
    channels,
    datasources,
    health,
    logs,
    mcp,
    pats,
    providers,
    settings,
)
from src.modules.administration.api.auth import get_current_user
from src.modules.administration.api.settings import get_app_version
from src.modules.assistant import api as assistant_api
from src.modules.assistant import chat_api
from src.modules.assistant.task_runner import assistant_task_runner
from src.modules.automation.api import agents, suggestions, templates
from src.modules.market.api import (
    discovery,
    klines,
    market,
    news,
    price_alerts,
    quotes,
    stocks,
)
from src.modules.paper_trading.api import paper_trading
from src.modules.portfolio.api import accounts, dashboard, history
from src.modules.research.api import (
    context,
    evaluations,
    feedback,
    insights,
    recommendations,
)
from src.modules.strategy.api import factors
from src.web.response import ResponseWrapperMiddleware

app = FastAPI(
    title="PanWatch API",
    version="0.1.0",
    redirect_slashes=False,  # Tránh chuyển hướng làm mất header Authorization
)

app.add_middleware(ResponseWrapperMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Route xác thực (không cần đăng nhập)
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
# Chỉ số thị trường (dữ liệu công khai, không cần đăng nhập)
app.include_router(market.router, prefix="/api/market", tags=["market"])

# Các route bắt buộc đăng nhập
protected = [Depends(get_current_user)]
app.include_router(
    stocks.router, prefix="/api/stocks", tags=["stocks"], dependencies=protected
)
app.include_router(
    quotes.router, prefix="/api/quotes", tags=["quotes"], dependencies=protected
)
app.include_router(
    klines.router, prefix="/api/klines", tags=["klines"], dependencies=protected
)
app.include_router(
    insights.router, prefix="/api/insights", tags=["insights"], dependencies=protected
)
app.include_router(
    accounts.router, prefix="/api", tags=["accounts"], dependencies=protected
)
app.include_router(
    agents.router, prefix="/api/agents", tags=["agents"], dependencies=protected
)
app.include_router(
    providers.router,
    prefix="/api/providers",
    tags=["providers"],
    dependencies=protected,
)
app.include_router(
    channels.router, prefix="/api/channels", tags=["channels"], dependencies=protected
)
app.include_router(
    datasources.router,
    prefix="/api/datasources",
    tags=["datasources"],
    dependencies=protected,
)
app.include_router(
    settings.router, prefix="/api/settings", tags=["settings"], dependencies=protected
)
app.include_router(
    logs.router, prefix="/api/logs", tags=["logs"], dependencies=protected
)
app.include_router(
    history.router, prefix="/api", tags=["history"], dependencies=protected
)
app.include_router(
    context.router, prefix="/api", tags=["context"], dependencies=protected
)
app.include_router(
    evaluations.router,
    prefix="/api/evaluations",
    tags=["evaluations"],
    dependencies=protected,
)
app.include_router(
    news.router, prefix="/api/news", tags=["news"], dependencies=protected
)
app.include_router(
    suggestions.router,
    prefix="/api/suggestions",
    tags=["suggestions"],
    dependencies=protected,
)
app.include_router(
    templates.router,
    prefix="/api/templates",
    tags=["templates"],
    dependencies=protected,
)
app.include_router(
    feedback.router,
    prefix="/api/feedback",
    tags=["feedback"],
    dependencies=protected,
)

app.include_router(
    discovery.router,
    prefix="/api/discovery",
    tags=["discovery"],
    dependencies=protected,
)
app.include_router(
    price_alerts.router,
    prefix="/api/price-alerts",
    tags=["price-alerts"],
    dependencies=protected,
)
app.include_router(
    recommendations.router,
    prefix="/api/recommendations",
    tags=["recommendations"],
    dependencies=protected,
)
app.include_router(
    dashboard.router,
    prefix="/api/dashboard",
    tags=["dashboard"],
    dependencies=protected,
)
app.include_router(
    factors.router,
    prefix="/api/factors",
    tags=["factors"],
    dependencies=protected,
)
app.include_router(
    health.router,
    prefix="/api/health",
    tags=["health"],
    dependencies=protected,
)
app.include_router(
    paper_trading.router,
    prefix="/api/paper-trading",
    tags=["paper-trading"],
    dependencies=protected,
)
app.include_router(
    chat_api.router,
    prefix="/api/chat",
    tags=["chat"],
    dependencies=protected,
)
app.include_router(
    assistant_api.router,
    prefix="/api/assistant",
    tags=["assistant"],
    dependencies=protected,
)


app.router.on_startup.append(assistant_task_runner.recover_pending)
# Quản lý PAT (cần đăng nhập): tạo / liệt kê / thu hồi mã truy cập cá nhân dùng cho MCP
app.include_router(
    pats.router, prefix="/api/pats", tags=["pats"], dependencies=protected
)
# MCP Server: gắn ở cấp cao nhất /mcp (không nằm dưới /api/, đi vòng qua middleware bọc phản hồi để giữ nguyên dạng JSON-RPC),
# tự mang xác thực PAT, không dùng JWT đăng nhập
app.include_router(mcp.router, prefix="/mcp", tags=["mcp"])


@app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
@app.get(
    "/.well-known/oauth-protected-resource/{_resource_path:path}",
    include_in_schema=False,
)
def oauth_protected_resource_metadata(request: Request, _resource_path: str = ""):
    """Siêu dữ liệu RFC 9728: máy khách MCP dò điểm cuối này trước khi bắt tay để quyết cách xác thực.

    PanWatch dùng PAT tĩnh (không có OAuth server), trả về authorization_servers=[] +
    bearer_methods_supported=["header"], bảo máy khách dùng thẳng Authorization Bearer.
    Dù không dùng OAuth thì điểm cuối này vẫn phải có, nếu không máy khách nhận 404 sẽ báo
    lỗi vì schema không khớp.
    """
    base = str(request.base_url).rstrip("/")
    return {
        "resource": f"{base}/mcp",
        "authorization_servers": [],
        "bearer_methods_supported": ["header"],
    }


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/version")
async def version():
    """Lấy số phiên bản của ứng dụng (giao diện công khai)"""
    return {"version": get_app_version()}
