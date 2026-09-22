"""MCP Server — phơi 5 công cụ chỉ đọc của chat thành endpoint Model Context Protocol.

Các lựa chọn thiết kế (có giải thích trong báo cáo):
- **Tự viết JSON-RPC nhẹ** (chế độ phản hồi JSON của Streamable HTTP), không kéo SDK mcp
  vào — phụ thuộc tối thiểu, kiểm thử tự chứa, bề mặt giao thức nhỏ (kịch bản chỉ đọc chỉ
  cần initialize / tools/list / tools/call);
- Gắn ở **cấp cao nhất `/mcp`** (không nằm dưới `/api/`) để đi vòng qua lớp bọc
  `{code,data,message}` của ResponseWrapperMiddleware, bảo đảm gói tin JSON-RPC được trả
  nguyên dạng;
- Xác thực bằng **hệ PAT riêng** (tiền tố pwmcp_ + lưu sha256 + so sánh thời gian hằng
  định + scope mcp:read), tách khỏi JWT đăng nhập; công cụ toàn chỉ đọc nên tự thân đã an
  toàn; mỗi lời gọi đều ghi nhật ký kiểm toán.

Phần cài đặt công cụ tái dùng schema công khai và dispatcher của assistant, không viết lại
logic nghiệp vụ.
"""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from src.modules.administration.pat import (
    SCOPE_MCP_READ,
    hash_token,
    looks_like_pat,
    verify_pat_hash,
)
from src.modules.assistant.legacy_chat_tools import CHAT_TOOLS, execute_chat_tool
from src.platform.persistence.database import SessionLocal, get_db
from src.platform.persistence.models import MCPCallLog, PersonalAccessToken

logger = logging.getLogger(__name__)
router = APIRouter()

# Phiên bản giao thức (giá trị mặc định khi máy khách không thương lượng)
DEFAULT_PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "PanWatch", "version": "0.1.0"}

# Danh sách trắng công cụ chỉ đọc (tái dùng định nghĩa công cụ của chat, công cụ mới tự động được tính vào)
READ_TOOL_NAMES = {t["function"]["name"] for t in CHAT_TOOLS}

# Cửa sổ giãn ghi last_used (giây), tránh ghi xuống cơ sở dữ liệu ở mỗi lần gọi công cụ
_LAST_USED_THROTTLE_S = 60
# Giới hạn độ dài phần tóm tắt nhật ký kiểm toán
_ARG_SUMMARY_MAX = 200
_ARG_VALUE_MAX = 40


# ──────────────── Xác thực PAT ────────────────


def _to_utc(dt: datetime | None) -> datetime | None:
    """DateTime mà SQLite lưu không mang múi giờ, nên thống nhất xử lý theo UTC để tránh lỗi khi so sánh giá trị có và không có múi giờ."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _bump_last_used(db: Session, row: PersonalAccessToken, request: Request) -> None:
    now = datetime.now(timezone.utc)
    last = _to_utc(row.last_used_at)
    if last is None or (now - last).total_seconds() > _LAST_USED_THROTTLE_S:
        row.last_used_at = now.replace(tzinfo=None)
        row.last_used_ip = request.client.host if request.client else None
        db.commit()


def authenticate_pat(request: Request, db: Session) -> dict:
    """Kiểm tra Authorization: Bearer pwmcp_..., trả về siêu dữ liệu của PAT; thất bại thì ném HTTPException."""
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "缺少 Bearer PAT")
    token = header[7:].strip()
    if not looks_like_pat(token):
        raise HTTPException(403, "MCP 端点需要 PAT(pwmcp_ 前缀)")

    row = (
        db.query(PersonalAccessToken)
        .filter(PersonalAccessToken.token_hash == hash_token(token))
        .first()
    )
    if row is None or not verify_pat_hash(token, row.token_hash):
        raise HTTPException(401, "无效的 token")
    if row.revoked_at is not None:
        raise HTTPException(401, "token 已吊销")
    exp = _to_utc(row.expires_at)
    if exp is not None and exp < datetime.now(timezone.utc):
        raise HTTPException(401, "token 已过期")

    try:
        scopes = set(json.loads(row.scopes_json or "[]"))
    except Exception:
        scopes = set()
    if SCOPE_MCP_READ not in scopes:
        raise HTTPException(403, f"PAT 缺少所需 scope: {SCOPE_MCP_READ}")

    _bump_last_used(db, row, request)
    return {
        "id": row.id,
        "prefix": row.prefix,
        "name": row.name,
        "client_ip": request.client.host if request.client else None,
    }


# ──────────────── Nhật ký kiểm toán ────────────────


def _summarize_args(args: dict) -> str | None:
    """Tóm tắt đã che thông tin nhạy cảm: các trường dạng k=v, có cắt bớt, phục vụ kiểm toán và gỡ lỗi."""
    if not args:
        return None
    parts: list[str] = []
    for k, v in args.items():
        if v is None:
            continue
        if isinstance(v, str):
            shown = v if len(v) <= _ARG_VALUE_MAX else v[: _ARG_VALUE_MAX - 1] + "…"
        elif isinstance(v, (list, tuple)):
            shown = f"[{len(v)}]"
        elif isinstance(v, dict):
            shown = f"{{{len(v)}}}"
        else:
            shown = repr(v)
        parts.append(f"{k}={shown}")
    summary = ", ".join(parts)
    return summary[:_ARG_SUMMARY_MAX] if summary else None


def _write_call_log(
    pat: dict,
    tool_name: str,
    status: str,
    error: str | None,
    args_summary: str | None,
    duration_ms: int,
    client_ip: str | None,
) -> None:
    """Ghi nhật ký kiểm toán (dùng session riêng, lỗi thì im lặng bỏ qua chứ không ảnh hưởng luồng chính)."""
    db = SessionLocal()
    try:
        db.add(
            MCPCallLog(
                pat_id=pat.get("id"),
                pat_prefix=pat.get("prefix"),
                tool_name=(tool_name or "")[:200],
                status=status,
                error_message=(str(error)[:500] if error else None),
                args_summary=args_summary,
                duration_ms=duration_ms,
                client_ip=client_ip,
            )
        )
        db.commit()
    except Exception:
        logger.warning("写入 MCPCallLog 失败", exc_info=True)
        db.rollback()
    finally:
        db.close()


MCP_LOG_RETENTION_DAYS = 30


def prune_mcp_logs(retention_days: int = MCP_LOG_RETENTION_DAYS) -> int:
    """Dọn nhật ký lời gọi MCP quá hạn lưu trữ, trả về số bản ghi đã xóa (để tác vụ lập lịch hằng ngày gọi)."""
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    db = SessionLocal()
    try:
        deleted = (
            db.query(MCPCallLog).filter(MCPCallLog.called_at < cutoff).delete()
        )
        db.commit()
        if deleted:
            logger.info("MCP 调用日志保留期清理: 删除 %d 条", deleted)
        return deleted
    except Exception:
        logger.warning("MCP 调用日志清理失败", exc_info=True)
        db.rollback()
        return 0
    finally:
        db.close()


# ──────────────── Xử lý JSON-RPC ────────────────


def _mcp_tools() -> list[dict]:
    """CHAT_TOOLS (schema function của OpenAI) → danh sách tool của MCP."""
    tools = []
    for t in CHAT_TOOLS:
        fn = t["function"]
        tools.append(
            {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "inputSchema": fn.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    return tools


def _rpc_result(req_id, result) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})


def _rpc_error(req_id, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    )


async def _handle_tools_call(params: dict, db: Session, pat: dict, req_id) -> JSONResponse:
    import time

    name = params.get("name")
    args = params.get("arguments") or {}
    if not name:
        return _rpc_error(req_id, -32602, "缺少工具名 name")
    if name not in READ_TOOL_NAMES:
        _write_call_log(pat, str(name), "error", "unknown tool", None, 0, pat.get("client_ip"))
        return _rpc_error(req_id, -32602, f"未知或不允许的工具: {name}")

    start = time.perf_counter()
    err: str | None = None
    try:
        text = await execute_chat_tool(db, name, args if isinstance(args, dict) else {})
        is_error = text.startswith("工具执行出错")
        if is_error:
            err = text
        return _rpc_result(
            req_id,
            {"content": [{"type": "text", "text": text}], "isError": is_error},
        )
    except Exception as e:  # noqa: BLE001 — bắt dự phòng, không để ngoại lệ xuyên qua tầng giao thức
        err = str(e)
        return _rpc_error(req_id, -32603, f"工具执行异常: {e}")
    finally:
        duration_ms = int((time.perf_counter() - start) * 1000)
        _write_call_log(
            pat,
            str(name),
            "error" if err else "ok",
            err,
            _summarize_args(args if isinstance(args, dict) else {}),
            duration_ms,
            pat.get("client_ip"),
        )


@router.post("")
@router.post("/")
async def mcp_endpoint(request: Request, db: Session = Depends(get_db)):
    """Endpoint đơn của MCP Streamable HTTP: xử lý initialize / tools/list / tools/call..."""
    pat = authenticate_pat(request, db)

    try:
        payload = await request.json()
    except Exception:
        return _rpc_error(None, -32700, "JSON 解析失败")

    if not isinstance(payload, dict):
        return _rpc_error(None, -32600, "仅支持单条 JSON-RPC 请求")

    method = payload.get("method")
    req_id = payload.get("id")
    params = payload.get("params") or {}

    # Thông điệp dạng notification (không có id) không cần phản hồi, trả 202
    if req_id is None and isinstance(method, str) and method.startswith("notifications/"):
        return Response(status_code=202)

    if method == "initialize":
        proto = params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION
        return _rpc_result(
            req_id,
            {
                "protocolVersion": proto,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "ping":
        return _rpc_result(req_id, {})
    if method == "tools/list":
        return _rpc_result(req_id, {"tools": _mcp_tools()})
    if method == "tools/call":
        return await _handle_tools_call(params, db, pat, req_id)

    return _rpc_error(req_id, -32601, f"方法不支持: {method}")
