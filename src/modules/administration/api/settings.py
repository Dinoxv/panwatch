import base64
import os
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.platform.persistence.database import get_db
from src.platform.persistence.models import AppSettings
from src.platform.runtime.config import Settings
from src.modules.administration.update_checker import check_update

router = APIRouter()

# Router của module không còn nằm ở thư mục nông sát gốc repo; tệp phiên bản phải suy ra từ vị trí tuyệt đối của chính tệp này,
# không được dựa vào thư mục làm việc hiện tại của tiến trình dịch vụ.
VERSION_FILE = Path(__file__).resolve().parents[4] / "VERSION"


def get_app_version() -> str:
    """获取应用版本号"""
    # Ưu tiên đọc từ biến môi trường
    version = os.getenv("APP_VERSION")
    if version:
        return version

    # Đọc từ tệp VERSION (hỗ trợ nhiều vị trí)
    possible_paths = [Path("VERSION"), VERSION_FILE]
    for path in possible_paths:
        try:
            return path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            continue
    return "dev"


class SettingUpdate(BaseModel):
    value: str


class SettingResponse(BaseModel):
    key: str
    value: str
    description: str

    class Config:
        from_attributes = True


# Mô tả mục cấu hình
SETTING_DESCRIPTIONS = {
    "http_proxy": "HTTP 代理地址(配置后所有对外请求含行情/新闻/AI/通知统一走此代理)",
    "notify_quiet_hours": "通知静默时间段（HH:MM-HH:MM，空为关闭）",
    "notify_retry_attempts": "通知失败重试次数（不含首次）",
    "notify_retry_backoff_seconds": "通知重试退避秒数（基数）",
    "notify_dedupe_ttl_overrides": "通知幂等窗口覆盖（JSON，空为默认）",
    "stock_link_platform": "股票链接平台（点击股票代码跳转的行情网站）",
    "panwatch_base_url": "PanWatch 公开访问地址（用于通知里的分析详情页链接，如 https://panwatch.example.com）",
}

SETTING_KEYS = list(SETTING_DESCRIPTIONS.keys())


def _get_env_defaults() -> dict[str, str]:
    """从 .env / 环境变量读取当前值作为默认"""
    s = Settings()
    return {
        "http_proxy": s.http_proxy,
        "notify_quiet_hours": s.notify_quiet_hours,
        "notify_retry_attempts": str(s.notify_retry_attempts),
        "notify_retry_backoff_seconds": str(s.notify_retry_backoff_seconds),
        "notify_dedupe_ttl_overrides": s.notify_dedupe_ttl_overrides,
        "stock_link_platform": "xueqiu",
        "panwatch_base_url": os.getenv("PANWATCH_BASE_URL", ""),
    }


@router.get("", response_model=list[SettingResponse])
def list_settings(db: Session = Depends(get_db)):
    settings = db.query(AppSettings).all()
    existing_map = {s.key: s for s in settings}

    env_defaults = _get_env_defaults()

    result = []
    for key in SETTING_KEYS:
        desc = SETTING_DESCRIPTIONS.get(key, "")
        env_val = env_defaults.get(key, "")

        if key not in existing_map:
            s = AppSettings(key=key, value=env_val, description=desc)
            db.add(s)
            result.append(s)
        else:
            s = existing_map[key]
            if not s.description:
                s.description = desc
            result.append(s)
    db.commit()

    return result


AVATAR_KEY = "ui_avatar"  # Cơ sở dữ liệu chỉ lưu tên tệp; ảnh thật nằm ở data/avatars/


def _avatar_dir() -> str:
    d = os.path.join(os.environ.get("DATA_DIR", "./data"), "avatars")
    os.makedirs(d, exist_ok=True)
    return d


@router.get("/avatar")
def get_avatar(db: Session = Depends(get_db)):
    """读取用户头像:DB 存文件名,图片本体在 data/avatars/,读取后以 data URL 返回。

    GET /avatar 无同名 GET /{key},不存在路由抢匹配问题。
    """
    row = db.query(AppSettings).filter(AppSettings.key == AVATAR_KEY).first()
    fname = (row.value if row and row.value else "").strip()
    if not fname:
        return {"value": ""}
    path = os.path.join(_avatar_dir(), fname)
    if not os.path.isfile(path):
        return {"value": ""}
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return {"value": ""}
    mime = "image/png" if fname.lower().endswith(".png") else "image/jpeg"
    return {"value": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"}


@router.put("/avatar")
def set_avatar(update: SettingUpdate, db: Session = Depends(get_db)):
    """保存/清空用户头像:把 data URL 落成 data/avatars/avatar.* 文件,DB 仅记文件名。

    需在 /{key} 之前注册以优先匹配。传空字符串即清空(删文件 + 清记录)。
    """
    row = db.query(AppSettings).filter(AppSettings.key == AVATAR_KEY).first()
    old = (row.value if row else "") or ""
    value = (update.value or "").strip()

    if not value:
        if old:
            try:
                os.remove(os.path.join(_avatar_dir(), old))
            except OSError:
                pass
        if row:
            row.value = ""
        db.commit()
        return {"value": ""}

    if not (value.startswith("data:") and "," in value):
        raise HTTPException(400, "头像需为 data URL")
    header, b64 = value.split(",", 1)
    ext = "png" if "image/png" in header else "jpg"
    try:
        raw = base64.b64decode(b64)
    except Exception:
        raise HTTPException(400, "头像数据无效")

    fname = f"avatar.{ext}"
    with open(os.path.join(_avatar_dir(), fname), "wb") as f:
        f.write(raw)
    if old and old != fname:  # Xóa tệp cũ khi phần mở rộng thay đổi
        try:
            os.remove(os.path.join(_avatar_dir(), old))
        except OSError:
            pass
    if not row:
        row = AppSettings(key=AVATAR_KEY, value=fname, description="用户头像文件名")
        db.add(row)
    else:
        row.value = fname
    db.commit()
    return {"value": fname}


@router.put("/{key}", response_model=SettingResponse)
def update_setting(key: str, update: SettingUpdate, db: Session = Depends(get_db)):
    setting = db.query(AppSettings).filter(AppSettings.key == key).first()
    if not setting:
        desc = SETTING_DESCRIPTIONS.get(key, "")
        setting = AppSettings(key=key, value=update.value, description=desc)
        db.add(setting)
    else:
        setting.value = update.value

    db.commit()
    db.refresh(setting)

    # Đổi http_proxy phản ánh ngay vào env của tiến trình, mọi httpx (trust_env=True) dùng proxy mới mà không cần khởi động lại
    if key == "http_proxy":
        try:
            from server import apply_proxy_env
            apply_proxy_env(update.value)
        except Exception:
            pass

    return setting


@router.get("/version")
def get_version():
    """获取应用版本号"""
    return {"version": get_app_version()}


@router.get("/update-check")
def get_update_check(db: Session = Depends(get_db)):
    """检查是否有可用新版本（带服务端缓存）。"""
    current = get_app_version()
    app_proxy = (
        db.query(AppSettings)
        .filter(AppSettings.key == "http_proxy")
        .first()
    )
    proxy = (app_proxy.value if app_proxy and app_proxy.value else "").strip() or (
        Settings().http_proxy or ""
    )
    result = check_update(current, proxy=proxy)
    err = str(result.get("error") or "").strip()
    if err:
        return {
            "success": False,
            "code": 10061,
            "message": err,
        }
    return result
