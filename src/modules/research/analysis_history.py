"""Quản lý bản ghi lịch sử phân tích"""
import logging
import re
from datetime import date, datetime, timedelta

from src.modules.automation.agent_catalog import infer_agent_kind
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import AnalysisHistory
from src.platform.persistence.json_safe import to_jsonable

logger = logging.getLogger(__name__)

# agent_name của phân tích chuyên sâu TradingAgents trong AnalysisHistory (xem agent.py: name = "tradingagents")
TA_AGENT_NAME = "tradingagents"


def save_analysis(
    agent_name: str,
    stock_symbol: str,
    content: str,
    title: str = "",
    raw_data: dict | None = None,
    analysis_date: date | None = None,
) -> bool:
    """
    Lưu kết quả phân tích

    - Cùng một ngày thì ghi đè được
    - Bản ghi lịch sử không ghi đè được (đã chặn bằng ràng buộc cơ sở dữ liệu)

    Args:
        agent_name: tên Agent, ví dụ "daily_report"
        stock_symbol: mã cổ phiếu, "*" nghĩa là phân tích toàn cục
        content: nội dung phân tích của AI
        title: tiêu đề phân tích
        raw_data: ảnh chụp dữ liệu gốc
        analysis_date: ngày phân tích, mặc định hôm nay

    Returns:
        lưu thành công hay không
    """
    if analysis_date is None:
        analysis_date = date.today()

    date_str = analysis_date.strftime("%Y-%m-%d")

    db = SessionLocal()
    try:
        payload = to_jsonable(raw_data or {})
        agent_kind = infer_agent_kind(agent_name)

        # Tìm xem đã tồn tại chưa
        existing = db.query(AnalysisHistory).filter(
            AnalysisHistory.agent_name == agent_name,
            AnalysisHistory.stock_symbol == stock_symbol,
            AnalysisHistory.analysis_date == date_str,
        ).first()

        if existing:
            # Cập nhật (trong cùng ngày thì ghi đè được)
            existing.title = title
            existing.content = content
            existing.raw_data = payload
            existing.agent_kind_snapshot = agent_kind
            logger.info(f"更新分析记录: {agent_name}/{stock_symbol}/{date_str}")
        else:
            # Thêm mới
            record = AnalysisHistory(
                agent_name=agent_name,
                stock_symbol=stock_symbol,
                analysis_date=date_str,
                title=title,
                content=content,
                raw_data=payload,
                agent_kind_snapshot=agent_kind,
            )
            db.add(record)
            logger.info(f"新增分析记录: {agent_name}/{stock_symbol}/{date_str}")

        db.commit()
        return True

    except Exception as e:
        logger.error(f"保存分析记录失败: {e}")
        db.rollback()
        return False
    finally:
        db.close()


def get_analysis(
    agent_name: str,
    stock_symbol: str,
    analysis_date: date | None = None,
) -> AnalysisHistory | None:
    """
    Lấy kết quả phân tích

    Args:
        agent_name: tên Agent
        stock_symbol: mã cổ phiếu
        analysis_date: ngày phân tích, mặc định hôm nay

    Returns:
        bản ghi phân tích, hoặc None
    """
    if analysis_date is None:
        analysis_date = date.today()

    date_str = analysis_date.strftime("%Y-%m-%d")

    db = SessionLocal()
    try:
        return db.query(AnalysisHistory).filter(
            AnalysisHistory.agent_name == agent_name,
            AnalysisHistory.stock_symbol == stock_symbol,
            AnalysisHistory.analysis_date == date_str,
        ).first()
    finally:
        db.close()


def get_latest_analysis(
    agent_name: str,
    stock_symbol: str,
    before_date: date | None = None,
) -> AnalysisHistory | None:
    """
    Lấy kết quả phân tích gần nhất (dùng để lấy phân tích hôm qua/lịch sử)

    Args:
        agent_name: tên Agent
        stock_symbol: mã cổ phiếu
        before_date: bản ghi gần nhất trước ngày này, mặc định hôm nay

    Returns:
        bản ghi phân tích, hoặc None
    """
    if before_date is None:
        before_date = date.today()

    date_str = before_date.strftime("%Y-%m-%d")

    db = SessionLocal()
    try:
        return db.query(AnalysisHistory).filter(
            AnalysisHistory.agent_name == agent_name,
            AnalysisHistory.stock_symbol == stock_symbol,
            AnalysisHistory.analysis_date < date_str,
        ).order_by(AnalysisHistory.analysis_date.desc()).first()
    finally:
        db.close()


def get_analysis_history(
    agent_name: str,
    stock_symbol: str | None = None,
    limit: int = 30,
) -> list[AnalysisHistory]:
    """
    Lấy danh sách lịch sử phân tích

    Args:
        agent_name: tên Agent
        stock_symbol: mã cổ phiếu, None nghĩa là tất cả
        limit: giới hạn số bản ghi trả về

    Returns:
        danh sách bản ghi phân tích, xếp theo ngày giảm dần
    """
    db = SessionLocal()
    try:
        query = db.query(AnalysisHistory).filter(
            AnalysisHistory.agent_name == agent_name,
        )

        if stock_symbol:
            query = query.filter(AnalysisHistory.stock_symbol == stock_symbol)

        return query.order_by(AnalysisHistory.analysis_date.desc()).limit(limit).all()
    finally:
        db.close()


def get_latest_ta_verdict_row(
    symbol: str,
    within_days: int = 14,
    today: date | None = None,
) -> AnalysisHistory | None:
    """Lấy bản ghi phân tích chuyên sâu TradingAgents gần nhất của một mã (gồm cả hôm nay).

    get_latest_analysis dùng ngữ nghĩa ``analysis_date < before_date`` nên loại mất hôm nay,
    ở đây truyền ``before_date = today + 1 ngày`` để đưa cả hôm nay vào.

    Args:
        symbol: mã cổ phiếu
        within_days: chỉ còn hiệu lực trong bấy nhiêu ngày (quá thì coi là hết hạn, bên gọi tự xét)
        today: test tiêm vào được, mặc định date.today()

    Returns:
        dòng AnalysisHistory gần nhất, hoặc None.
    """
    if today is None:
        today = date.today()
    # +1 ngày để bao gồm hôm nay (get_latest_analysis dùng phép nhỏ hơn nghiêm ngặt)
    return get_latest_analysis(
        TA_AGENT_NAME, symbol, before_date=today + timedelta(days=1)
    )


def _clean_one_liner(text: str, max_chars: int = 120) -> str:
    """Lọc từ phần thân kết luận ra một câu tóm tắt rồi cắt về khoảng max_chars.

    - Bỏ dấu Markdown / khoảng trắng thừa / ký tự điều khiển
    - Lấy đoạn đầu (tới dấu chấm hoặc dấu xuống dòng đầu tiên)
    - Quá dài thì cắt và thêm dấu ba chấm
    """
    if not text:
        return ""
    s = str(text)
    # Bỏ ký tự nhấn mạnh markdown, dấu thăng tiêu đề, phần liên kết còn sót
    s = re.sub(r"[#*`>\-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return ""
    # Lấy câu đầu (dấu chấm kiểu Trung / Latin hoặc xuống dòng)
    m = re.split(r"[。\.!！\n]", s, maxsplit=1)
    head = (m[0] or s).strip()
    candidate = head if len(head) >= 8 else s
    if len(candidate) > max_chars:
        candidate = candidate[:max_chars].rstrip() + "…"
    return candidate


def get_latest_ta_verdict(
    symbol: str,
    within_days: int = 14,
    today: date | None = None,
) -> dict | None:
    """Rút bản gọn của kết luận chuyên sâu TA gần nhất cho một mã (làm tiên nghiệm trọng số cao cho phần trước/sau phiên).

    Chỉ trả về ``{rating, action_label, one_liner, date, age_days}`` — tuyệt đối không trả
    toàn văn, để khống chế ngân sách token. Thiếu dữ liệu / lỗi khi đọc → None (fail-soft, không ném).

    Args:
        symbol: mã cổ phiếu
        within_days: chỉ nhận bản ghi trong bấy nhiêu ngày (kể cả hôm nay), hết hạn thì trả None
        today: dùng để test tiêm vào, mặc định hôm nay
    """
    if today is None:
        today = date.today()
    try:
        row = get_latest_ta_verdict_row(symbol, within_days=within_days, today=today)
        if row is None:
            return None

        date_str = str(getattr(row, "analysis_date", "") or "")
        try:
            row_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

        age_days = (today - row_date).days
        if age_days < 0 or age_days > max(1, int(within_days)):
            return None

        raw = getattr(row, "raw_data", None) or {}
        if not isinstance(raw, dict):
            return None
        sug = raw.get("suggestion") or {}
        if not isinstance(sug, dict):
            sug = {}

        rating = sug.get("rating_raw") or raw.get("rating") or sug.get("action") or "hold"
        action_label = sug.get("action_label") or ""
        reason = sug.get("reason") or getattr(row, "content", "") or ""
        one_liner = _clean_one_liner(reason)

        return {
            "rating": str(rating),
            "action_label": str(action_label),
            "one_liner": one_liner,
            "date": date_str,
            "age_days": int(age_days),
        }
    except Exception as e:  # Mọi sự cố đều hạ cấp mềm
        logger.debug(f"提取 TA 深度结论失败: {symbol} - {e}")
        return None
