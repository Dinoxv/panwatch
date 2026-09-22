"""Bản ghi lượt chạy Agent - ghi vào bảng agent_runs (cho giao diện tra cứu)"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import AgentRun, LogEntry

logger = logging.getLogger(__name__)

# Giai đoạn thu thập có thể tạm thời không có nhật ký tiến độ khi nguồn ngoài giới hạn tốc độ / thử lại, nên không dùng được
# quy tắc “5 phút không có nhật ký là cũ”; nhưng sau khi dịch vụ khởi động lại cũng không được khôi phục tác vụ cũ vô hạn.
ACTIVE_RUN_TTL_SEC = 45 * 60


def _as_utc(value: datetime | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def start_agent_run(
    agent_name: str,
    trace_id: str,
    trigger_source: str = "",
    model_label: str = "",
) -> None:
    """Ghi bản ghi vòng đời running trước khi tác vụ thật sự bắt đầu.

    Cùng một trace có thể được gọi đồng thời từ lớp bọc API và từ lối vào thực thi, nên
    việc ghi là bất biến.
    """
    if not trace_id:
        return
    db = SessionLocal()
    try:
        existing = (
            db.query(AgentRun)
            .filter(AgentRun.trace_id == trace_id, AgentRun.status == "running")
            .order_by(AgentRun.id.desc())
            .first()
        )
        if existing:
            return
        db.add(AgentRun(
            agent_name=agent_name,
            status="running",
            trace_id=trace_id[:64],
            trigger_source=(trigger_source or "")[:32],
            model_label=(model_label or "")[:255],
        ))
        db.commit()
    except Exception as e:
        logger.warning(f"写入 AgentRun running 状态失败: {e}")
        db.rollback()
    finally:
        db.close()


def record_agent_run(
    agent_name: str,
    status: str,
    result: str = "",
    error: str = "",
    duration_ms: int = 0,
    trace_id: str = "",
    trigger_source: str = "",
    notify_attempted: bool = False,
    notify_sent: bool = False,
    context_chars: int = 0,
    model_label: str = "",
) -> None:
    """Ghi kết quả một lượt chạy Agent xuống cơ sở dữ liệu.

    Args:
        agent_name: tên Agent
        status: success / failed
        result: kết quả tóm tắt (sẽ bị cắt bớt)
        error: thông tin lỗi (sẽ bị cắt bớt)
        duration_ms: thời gian chạy (mili giây)
        trace_id: id truy vết chuỗi chạy
        trigger_source: schedule / manual / api
        notify_attempted: có thử gửi thông báo không
        notify_sent: thông báo gửi thành công không
        context_chars: số ký tự của prompt/context
        model_label: mã định danh mô hình dùng cho lượt chạy này
    """
    db = SessionLocal()
    try:
        existing = None
        if trace_id:
            existing = (
                db.query(AgentRun)
                .filter(AgentRun.trace_id == trace_id, AgentRun.status == "running")
                .order_by(AgentRun.id.desc())
                .first()
            )
        values = {
            "agent_name": agent_name,
            "status": status,
            "trace_id": (trace_id or "")[:64],
            "trigger_source": (trigger_source or "")[:32],
            "notify_attempted": bool(notify_attempted),
            "notify_sent": bool(notify_sent),
            "context_chars": max(0, int(context_chars or 0)),
            "model_label": (model_label or "")[:255],
            "result": (result or "")[:2000],
            "error": (error or "")[:2000],
            "duration_ms": duration_ms,
        }
        if existing:
            for key, value in values.items():
                setattr(existing, key, value)
        else:
            db.add(AgentRun(**values))
        db.commit()
    except Exception as e:
        logger.warning(f"写入 AgentRun 失败: {e}")
        db.rollback()
    finally:
        db.close()


def find_active_tradingagents_trace(db: Session, stock_symbol: str) -> str | None:
    """Trả về trace TradingAgents còn đang chạy của một mã, dùng cho việc kích hoạt bất biến giữa các module.

    Trạng thái chạy thuộc về module tự động hóa, module thị trường chỉ được dùng truy vấn
    công khai này để xét có cần tạo tác vụ mới không, không được import router HTTP của
    tự động hóa hay tra thẳng phần cài đặt bên trong của nó.
    """
    now = datetime.now(timezone.utc)

    # Bản ghi vòng đời là nguồn dữ liệu ưu tiên: khôi phục được cả khi giai đoạn thu thập chưa có ta_progress,
    # và không kích hoạt lại tác vụ chỉ vì một nguồn ngoài im lặng 5 phút.
    active_run = (
        db.query(AgentRun)
        .filter(
            AgentRun.agent_name == "tradingagents",
            AgentRun.status == "running",
            AgentRun.trace_id.like(f"%-{stock_symbol}-%"),
        )
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .first()
    )
    if active_run and active_run.trace_id:
        created_at = _as_utc(active_run.created_at)
        if created_at is None or (now - created_at).total_seconds() <= ACTIVE_RUN_TTL_SEC:
            return active_run.trace_id
        # Khi đã vượt cửa sổ an toàn của cả tác vụ thì nhật ký cũ không được phép đưa nó về lại trạng thái running.
        return None

    cutoff = now - timedelta(minutes=30)
    latest_log = (
        db.query(LogEntry)
        .filter(
            LogEntry.event == "ta_progress",
            LogEntry.agent_name == "tradingagents",
            LogEntry.timestamp >= cutoff,
            LogEntry.trace_id.like(f"%-{stock_symbol}-%"),
        )
        .order_by(LogEntry.timestamp.desc())
        .first()
    )
    if not latest_log or not latest_log.trace_id:
        return None

    trace_id = latest_log.trace_id
    run = (
        db.query(AgentRun)
        .filter(AgentRun.trace_id == trace_id)
        .order_by(AgentRun.id.desc())
        .first()
    )
    if run and run.status in ("success", "failed"):
        return None

    last_ts = _as_utc(latest_log.timestamp)
    if last_ts and (now - last_ts).total_seconds() > 300:
        return None
    return trace_id
