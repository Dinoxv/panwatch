"""Tự kiểm tra hệ thống (Doctor): khám nhanh nguồn dữ liệu / AI / thông báo, kèm gợi ý khắc phục.

Tái dùng đúng logic kiểm thử sẵn có của từng phần (nguồn dữ liệu dùng manager.test_source,
AI dùng AIClient.chat, thông báo dùng NotifierManager) chứ không dựng lại phép thăm dò;
chỉ bổ sung hai việc: ① chạy song song rồi gộp thành một bảng theo dõi ② quy các lỗi
thường gặp thành gợi ý khắc phục hành động được.

Phần thông báo mặc định **chỉ kiểm tra cấu hình URI chứ không gửi thật** (tránh spam);
phải đặt notify_send=True mới gửi thật.
"""

from __future__ import annotations

import asyncio
import logging
import time

from src.platform.persistence.database import SessionLocal

logger = logging.getLogger(__name__)

SLOW_MS = 4000          # Vượt mức này thì tính là «chậm»
PROBE_TIMEOUT_S = 20    # Thời gian chờ tối đa cho một lần thăm dò


def classify_hint(category: str, error: str | None) -> str:
    """Quy lỗi thành gợi ý khắc phục hành động được. Phủ các bẫy proxy / xác thực / cấu hình thường gặp nhất khi tự vận hành."""
    e = (error or "").lower()
    if category == "datasource":
        if "database is locked" in e:
            return "SQLite 被锁:并发调度叠加慢代理所致,降低并发或加快/关闭代理。"
        if any(k in e for k in (
            "server disconnected", "timeout", "timed out", "connect", "proxy",
            "ssl", "remote end closed", "read timed out", "connection reset",
        )):
            return "行情/新闻接口连接失败:所有请求走 http_proxy 设置的系统代理,检查该代理能否代出目标域名(国内接口需 CN 出口、Yahoo 需境外),或本机被 MITM 代理拦截需换可信出口。"
        return "数据源不通:打开数据源配置页看详细日志,确认 provider 与接口可达。"
    if category == "ai":
        if any(k in e for k in ("401", "unauthorized", "invalid_api_key", "api key", "incorrect api key", "authentication")):
            return "AI 鉴权失败:API Key 不对或失效,检查服务商 api_key。"
        if any(k in e for k in ("model", "not found", "does not exist", "404")):
            return "模型不存在:检查模型名(model)是否与服务商一致。"
        if any(k in e for k in ("429", "rate limit", "quota", "insufficient", "balance")):
            return "被限流或额度不足:稍后重试,或检查账户余额/额度。"
        if any(k in e for k in ("connect", "timeout", "timed out", "proxy", "ssl", "getaddrinfo", "name resolution")):
            return "连不上 AI 服务:检查 base_url 是否正确、是否需要/误用了代理。"
        return "AI 调用失败:逐项检查 base_url / api_key / model 配置。"
    if category == "notify":
        if any(k in e for k in ("invalid", "unsupported", "scheme", "malformed", "parse", "config")):
            return "通知配置无效:检查渠道 URL/参数格式(Apprise URI)。"
        if any(k in e for k in ("forbidden", "unauthorized", "403", "401", "404", "blocked", "connect", "timeout")):
            return "通知发送失败:检查 webhook 地址/token 是否正确、是否被网络拦截。"
        return "通知不通:核对渠道配置,或到渠道页点「测试」做真实发送验证。"
    if category == "system":
        if "lock" in e:
            return "SQLite 被锁:并发调度叠加慢代理所致,降低并发或加快/关闭代理。"
        if any(k in e for k in ("disk", "space", "磁盘", "空间")):
            return "磁盘空间不足:清理 data 目录旧数据/日志,或扩容磁盘。"
        if any(k in e for k in ("scheduler", "调度", "stopped", "not running")):
            return "调度器未运行/已停止:重启服务以恢复定时任务。"
        return error or "系统项异常,查看日志。"
    return error or "未知错误,查看日志。"


def _item(category: str, key: str, name: str, status: str,
          latency_ms: int, error: str | None = None, note: str | None = None) -> dict:
    return {
        "category": category,
        "key": key,
        "name": name,
        "status": status,  # ok | slow | fail
        "latency_ms": int(latency_ms),
        "error": error,
        "hint": classify_hint(category, error) if status == "fail" else "",
        "note": note,
    }


def _status_for(success: bool, latency_ms: int) -> str:
    if not success:
        return "fail"
    return "slow" if latency_ms > SLOW_MS else "ok"


async def probe_datasource(source) -> dict:
    """Tái dùng manager.test_source của collector."""
    from src.modules.market.data_collector import get_collector_manager

    t0 = time.monotonic()
    try:
        result = await get_collector_manager().test_source(source)
        latency = int(getattr(result, "duration_ms", None) or (time.monotonic() - t0) * 1000)
        return _item("datasource", f"ds:{source.id}", source.name,
                     _status_for(bool(result.success), latency), latency,
                     None if result.success else (result.error or "测试未通过"))
    except Exception as e:
        return _item("datasource", f"ds:{source.id}", source.name, "fail",
                     int((time.monotonic() - t0) * 1000), str(e))


async def probe_ai_model(model, service) -> dict:
    """Tái dùng AIClient.chat để gửi một lệnh ping cực ngắn."""
    from src.platform.ai.ai_client import AIClient

    name = model.name or model.model
    t0 = time.monotonic()
    try:
        client = AIClient(base_url=service.base_url, api_key=service.api_key, model=model.model)
        await client.chat(system_prompt="You are a helpful assistant.",
                          user_content="Say 'OK'.", temperature=0)
        latency = int((time.monotonic() - t0) * 1000)
        return _item("ai", f"ai:{model.id}", name, _status_for(True, latency), latency)
    except Exception as e:
        return _item("ai", f"ai:{model.id}", name, "fail",
                     int((time.monotonic() - t0) * 1000), str(e))


async def probe_notify_channel(channel, *, send: bool = False) -> dict:
    """Mặc định chỉ kiểm tra cấu hình URI (add_channel không thông sẽ ném lỗi); phải đặt send=True mới gửi thật."""
    from src.platform.notifications.notifier import NotifierManager

    name = channel.name or channel.type
    t0 = time.monotonic()
    try:
        notifier = NotifierManager()
        notifier.add_channel(channel.type, channel.config or {})  # URI không hợp lệ sẽ ném lỗi
        if not send:
            latency = int((time.monotonic() - t0) * 1000)
            return _item("notify", f"nc:{channel.id}", name, "ok", latency,
                         note="仅校验配置格式,未真实发送(勾选「含真实发送」可发测试消息)")
        result = await notifier.notify_with_result(
            title="系统自检", content="盯盘侠系统自检测试消息。", bypass_quiet_hours=True)
        latency = int((time.monotonic() - t0) * 1000)
        ok = bool(result.get("success"))
        return _item("notify", f"nc:{channel.id}", name, _status_for(ok, latency), latency,
                     None if ok else (result.get("error") or "发送失败"))
    except Exception as e:
        return _item("notify", f"nc:{channel.id}", name, "fail",
                     int((time.monotonic() - t0) * 1000), str(e))


async def probe_db() -> dict:
    """Chạy SELECT 1 trên cơ sở dữ liệu thật."""
    from sqlalchemy import text

    from src.platform.persistence.database import SessionLocal

    t0 = time.monotonic()
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
        latency = int((time.monotonic() - t0) * 1000)
        return _item("system", "sys:db", "数据库", _status_for(True, latency), latency)
    except Exception as e:
        return _item("system", "sys:db", "数据库", "fail", int((time.monotonic() - t0) * 1000), str(e))


async def probe_disk() -> dict:
    """Kiểm tra dung lượng trống của ổ đĩa chứa thư mục data."""
    import os
    import shutil

    from src.platform.persistence.database import DB_PATH

    t0 = time.monotonic()
    try:
        data_dir = os.path.dirname(os.path.abspath(DB_PATH))
        usage = shutil.disk_usage(data_dir)
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        note = f"可用 {free_gb:.1f}GB / 共 {total_gb:.1f}GB"
        latency = int((time.monotonic() - t0) * 1000)
        if free_gb < 0.2:
            return _item("system", "sys:disk", "磁盘空间", "fail", latency,
                         error=f"磁盘空间严重不足({note})", note=note)
        status = "slow" if free_gb < 1.0 else "ok"
        return _item("system", "sys:disk", "磁盘空间", status, latency, note=note)
    except Exception as e:
        return _item("system", "sys:disk", "磁盘空间", "fail", int((time.monotonic() - t0) * 1000), str(e))


async def probe_scheduler() -> dict:
    """Xem các bộ lập lịch đang chạy qua scheduler_registry; sổ đăng ký rỗng (chạy CLI / chưa khởi động) thì bỏ qua một cách êm."""
    from src.platform.scheduling import scheduler_registry

    regs = scheduler_registry.get_all()
    if not regs:
        return _item("system", "sys:scheduler", "调度器", "ok", 0,
                     note="当前进程无运行中的调度器(CLI 自检会跳过此项)")
    running: list[str] = []
    stopped: list[str] = []
    jobs = 0
    for name, sched in regs.items():
        try:
            if getattr(sched, "running", False):
                running.append(name)
                jobs += len(sched.get_jobs())
            else:
                stopped.append(name)
        except Exception:
            stopped.append(name)
    if running:
        note = f"{len(running)} 个调度器运行中,共 {jobs} 个任务"
        if stopped:
            note += f";已停止: {', '.join(stopped)}"
        return _item("system", "sys:scheduler", "调度器", "ok", 0, note=note)
    return _item("system", "sys:scheduler", "调度器", "fail", 0,
                 error=f"调度器已停止: {', '.join(stopped)}")


async def _guard(coro, fallback: dict) -> dict:
    """Bọc thời gian chờ cho từng phép thăm dò; bản thân phép thăm dò đã tự try/except nên ở đây chỉ bắt quá hạn và ngoại lệ."""
    try:
        return await asyncio.wait_for(coro, timeout=PROBE_TIMEOUT_S)
    except asyncio.TimeoutError:
        return _item(fallback["category"], fallback["key"], fallback["name"],
                     "fail", PROBE_TIMEOUT_S * 1000, f"探测超时(>{PROBE_TIMEOUT_S}s)")
    except Exception as e:  # pragma: no cover - nhánh phòng thủ
        return _item(fallback["category"], fallback["key"], fallback["name"],
                     "fail", 0, str(e))


def _enumerate(db, include_system: bool = True) -> list[dict]:
    """Liệt kê mọi mục cần kiểm tra (định danh + tham chiếu ORM) mà không thăm dò. include_system thêm các mục hạ tầng DB / đĩa / lập lịch."""
    from src.platform.persistence.models import AIModel, AIService, DataSource, NotifyChannel

    targets: list[dict] = []
    if include_system:
        targets.append({"category": "system", "key": "sys:db", "name": "数据库", "group": None, "_kind": "db"})
        targets.append({"category": "system", "key": "sys:disk", "name": "磁盘空间", "group": None, "_kind": "disk"})
        targets.append({"category": "system", "key": "sys:scheduler", "name": "调度器", "group": None, "_kind": "sched"})
    for src in db.query(DataSource).filter(DataSource.enabled.is_(True)).all():
        targets.append({"category": "datasource", "key": f"ds:{src.id}", "name": src.name,
                        "group": None, "_kind": "ds", "_obj": src})
    for model in db.query(AIModel).all():
        service = db.query(AIService).filter(AIService.id == model.service_id).first()
        if not service:
            continue
        # group = tên nhà cung cấp, để giao diện dựng cây hai cấp «nhà cung cấp → mô hình»
        targets.append({"category": "ai", "key": f"ai:{model.id}", "name": model.name or model.model,
                        "group": service.name, "_kind": "ai", "_obj": model, "_service": service})
    for ch in db.query(NotifyChannel).filter(NotifyChannel.enabled.is_(True)).all():
        targets.append({"category": "notify", "key": f"nc:{ch.id}", "name": ch.name or ch.type,
                        "group": None, "_kind": "nc", "_obj": ch})
    return targets


def _identity(t: dict) -> dict:
    return {"category": t["category"], "key": t["key"], "name": t["name"], "group": t.get("group")}


def _probe_for(t: dict, notify_send: bool):
    kind = t["_kind"]
    if kind == "db":
        return probe_db()
    if kind == "disk":
        return probe_disk()
    if kind == "sched":
        return probe_scheduler()
    if kind == "ds":
        return probe_datasource(t["_obj"])
    if kind == "ai":
        return probe_ai_model(t["_obj"], t["_service"])
    return probe_notify_channel(t["_obj"], send=notify_send)


def list_selfcheck_items(*, db=None, include_system: bool = True) -> list[dict]:
    """Chỉ liệt kê định danh của các mục cần kiểm tra (category/key/name/group) mà không thăm dò; để giao diện dựng danh sách trước rồi kiểm tra từng mục sau."""
    own = db is None
    db = db or SessionLocal()
    try:
        return [_identity(t) for t in _enumerate(db, include_system)]
    finally:
        if own:
            db.close()


async def run_selfcheck(*, db=None, notify_send: bool = False, keys=None, include_system: bool = True) -> dict:
    """Thăm dò các mục cần kiểm tra rồi trả về bảng theo dõi. keys khác rỗng thì chỉ thăm dò đúng các key đó (để giao diện cập nhật tiến độ từng mục)."""
    own = db is None
    db = db or SessionLocal()
    try:
        keyset = set(keys) if keys is not None else None
        targets = [t for t in _enumerate(db, include_system) if keyset is None or t["key"] in keyset]
        tasks = [_guard(_probe_for(t, notify_send), _identity(t)) for t in targets]
        items = list(await asyncio.gather(*tasks)) if tasks else []
        summary = {
            "total": len(items),
            "ok": sum(1 for i in items if i["status"] == "ok"),
            "slow": sum(1 for i in items if i["status"] == "slow"),
            "fail": sum(1 for i in items if i["status"] == "fail"),
        }
        return {"items": items, "summary": summary, "notify_send": bool(notify_send)}
    finally:
        if own:
            db.close()
