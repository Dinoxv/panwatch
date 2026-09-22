"""Ánh xạ đầu ra của TradingAgents → AnalysisResult của PanWatch.

`final_state` của TradingAgents là dict do LangGraph tích lũy, các trường then chốt (trích từ thượng nguồn):
- market_report / social_report / news_report / fundamentals_report: báo cáo của 4 chuyên viên phân tích
- investment_debate_state: lịch sử tranh luận xem tăng xem giảm {history, current_response, judge_decision}
- trader_investment_plan: ý kiến của trader
- risk_judge_decision: phán quyết kiểm soát rủi ro
- final_trade_decision: bản quyết định cuối sau khi PM tổng hợp
- (processed_signal): "BUY" / "HOLD" / "SELL"

Sau khi kết quả phân tích ghi xuống kho, nửa dưới của tệp lo phần bắc cầu tín hiệu mô
phỏng (tùy chọn); cả hai dùng chung một bộ ánh xạ an toàn REVIEW.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

from src.modules.automation.base import AnalysisResult

__all__ = [
    "DECISION_LABEL_MAP",
    "map_state_to_result",
    "maybe_emit_paper_trading_signal",
]


# Thang 5 bậc của thượng nguồn → nhãn hiển thị của PanWatch
RATING_LABEL_MAP = {
    "buy": "买入",
    "overweight": "增持",
    "hold": "持有",
    "underweight": "减持",
    "sell": "卖出",
}

# 5 bậc → 3 bậc (cho trường action; giao diện dùng 'buy' | 'hold' | 'sell')
RATING_ACTION_MAP = {
    "buy": "buy",
    "overweight": "buy",
    "hold": "hold",
    "underweight": "sell",
    "sell": "sell",
}

# Từ 0.4.0, thượng nguồn trả REVIEW khi không bóc được xếp hạng của PM. Đây KHÔNG phải lệnh Nắm giữ có thể giao dịch:
# Không mở rộng API action 3 bậc của giao diện, dùng hold để chặn giao dịch tự động, đồng thời giữ trạng thái gốc để hiển thị / cảnh báo.
REVIEW_RATING = "review"
REVIEW_LABEL = "待人工复核"

# Tương thích tên trường cũ: một số mã hạ nguồn có thể import DECISION_LABEL_MAP
DECISION_LABEL_MAP = RATING_LABEL_MAP


def map_state_to_result(
    *,
    stock: Any,
    ta_result: dict[str, Any],
    model_label: str = "",
) -> AnalysisResult:
    """Lối vào chính: ánh xạ final_state của TradingAgents thành AnalysisResult.

    Args:
        stock: StockConfig của PanWatch (symbol/name/market)
        ta_result: {"decision": str, "final_state": dict, "cost_usd": float}
        model_label: dạng "deepseek/deepseek-chat", ghi ở cuối markdown
    """
    state = ta_result.get("final_state") or {}
    cost_usd = float(ta_result.get("cost_usd", 0.0) or 0.0)

    # Xếp hạng lấy phần thân của PM (final_trade_decision, tức bản quyết định cuối mà người dùng thực sự đọc) làm nguồn có thẩm quyền:
    # Giá trị decision thứ hai mà propagate() của thượng nguồn trả về là bản chắt lọc lần hai từ phần thân nên bị méo (thân ghi "卖出"
    # mà lại trả "HOLD"), nên ưu tiên bóc nhãn xếp hạng tường minh nằm trong phần thân.
    # Thứ tự ưu tiên: nhãn tường minh trong thân > decision của thượng nguồn > quét mờ trong thân làm dự phòng.
    final_text = state.get("final_trade_decision") or ""
    upstream_rating = (ta_result.get("decision") or "").strip().lower()
    if upstream_rating == REVIEW_RATING:
        # REVIEW là phán định của thượng nguồn rằng cả bản PM không bóc được, không được để từ xếp hạng trong thân ghi đè lên nó.
        rating_raw = REVIEW_RATING
    else:
        rating_raw = _parse_rating_label(final_text)
    if rating_raw not in RATING_LABEL_MAP and rating_raw != REVIEW_RATING:
        rating_raw = upstream_rating
    if rating_raw not in RATING_LABEL_MAP and rating_raw != REVIEW_RATING:
        rating_raw = _parse_rating_from_text(final_text)

    review_required = rating_raw == REVIEW_RATING
    action = RATING_ACTION_MAP.get(rating_raw, "hold")
    action_label = REVIEW_LABEL if review_required else RATING_LABEL_MAP.get(rating_raw, "持有")

    confidence = _extract_confidence(state, rating_raw)
    short_reason = _short_reason(state)

    suggestion = {
        "action": action,
        "action_label": action_label,
        "rating_raw": rating_raw or "hold",  # Giữ nguyên thang 5 bậc gốc để giao diện / lịch sử tra lại được
        "review_required": review_required,
        "upstream_decision": upstream_rating,
        "signal": _truncate(state.get("trader_investment_plan", ""), 200),
        "reason": state.get("final_trade_decision") or short_reason,
        "should_alert": review_required or rating_raw in ("buy", "overweight", "underweight", "sell"),
        "agent_name": "tradingagents",
        "agent_label": "TradingAgents 深度",
        "confidence": confidence,
    }

    content = _render_markdown(state, suggestion, model_label, cost_usd)
    # Liên kết bấm được tới trang chi tiết (chỉ hiện khi đã cấu hình panwatch_base_url)
    from datetime import date as _date
    from src.modules.research.analysis_link import analysis_detail_markdown
    _link = analysis_detail_markdown(stock.symbol, _date.today().isoformat())
    if _link:
        content = content.rstrip() + f"\n\n---\n{_link}"
    # Thân thông báo chỉ chứa «quyết định cuối» (tóm tắt quyết định + bản quyết định của PM) + liên kết chi tiết;
    # toàn văn của trader / trưởng bộ phận nghiên cứu / tranh luận quản trị rủi ro / bốn chuyên viên đều nằm ở trang chi tiết, tránh thông báo quá dài bị cắt.
    notify_content = _render_notify(state, suggestion, cost_usd, _link)

    return AnalysisResult(
        agent_name="tradingagents",
        title=f"【深度】{stock.name}({stock.symbol}):{suggestion['action_label']}",
        content=content,
        notify_content=notify_content,
        raw_data={
            "suggestion": suggestion,
            "cost_usd": cost_usd,
            "should_alert": suggestion["should_alert"],
            "decision": action,           # Tương thích trường cũ (3 bậc)
            "rating": rating_raw or "hold",  # Trường mới (5 bậc nguyên bản)
            "upstream_decision": upstream_rating,
            "confidence": confidence,
            "debate_history": _extract_debate(state),
            "risk_judgment": _risk_judgment(state),
            "risk_debate": _extract_risk_debate(state),
            "analyst_reports": {
                "market": state.get("market_report") or "",
                # Trường của chuyên viên phân tích tâm lý ở thượng nguồn là sentiment_report; vẫn tương thích social_report cũ
                "social": state.get("sentiment_report") or state.get("social_report") or "",
                "news": state.get("news_report") or "",
                "fundamentals": state.get("fundamentals_report") or "",
            },
            "final_decision": state.get("final_trade_decision") or "",
            "trader_plan": state.get("trader_investment_plan") or "",
        },
    )


# ---- helpers ----


# Lớp ký tự phân tách phủ cả dấu nửa chiều rộng (: -) lẫn toàn chiều rộng (： －) — bản PM tiếng Trung thật dùng dấu hai chấm toàn chiều rộng "：",
# bản đầu chỉ nhận ":" nửa chiều rộng nên "最终交易决策：Buy" không khớp được, phải lùi về decision đã méo của thượng nguồn.
_RATING_TEXT_RE = re.compile(
    r"(?:Rating|评级|最终交易决策|Final\s+(?:Trade\s+)?Decision|FINAL\s+TRANSACTION\s+PROPOSAL)"
    r"[\s\*:：\-—－]+(\*\*)?\s*(Buy|Overweight|Hold|Underweight|Sell|买入|增持|持有|减持|卖出)",
    re.I,
)
_RATING_ZH_TO_EN = {
    "买入": "buy", "增持": "overweight", "持有": "hold",
    "减持": "underweight", "卖出": "sell",
}


def _parse_rating_label(text: str) -> str:
    """Chỉ đọc **nhãn xếp hạng tường minh** trong phần thân của PM (quyết định giao dịch cuối/xếp hạng/FINAL TRANSACTION PROPOSAL: X).

    Không quét từ khóa kiểu mờ — tránh những cụm gây nhiễu như "đã bác bỏ khuyến nghị mua
    trước đó" trong phần thân bị xét nhầm. Dùng làm lựa chọn đầu tiên khi rút xếp hạng, để
    phần hiển thị khớp với bản quyết định cuối mà người dùng nhìn thấy.
    """
    if not text:
        return ""
    m = _RATING_TEXT_RE.search(text)
    if m:
        word = m.group(2).lower()
        if word in _RATING_ZH_TO_EN:
            word = _RATING_ZH_TO_EN[word]
        if word in RATING_LABEL_MAP:
            return word
    return ""


def _parse_rating_from_text(text: str) -> str:
    """Rút xếp hạng năm bậc từ văn bản. Ưu tiên nhãn 'Rating: X', rồi tới từ năm bậc đầu tiên."""
    if not text:
        return ""
    label = _parse_rating_label(text)
    if label:
        return label
    # Dự phòng: quét cả đoạn tìm từ thuộc thang 5 bậc xuất hiện đầu tiên, tiếng Anh hoặc tiếng Trung
    text_low = text.lower()
    for word in ("overweight", "underweight", "buy", "sell", "hold"):
        if word in text_low:
            return word
    for zh, en in _RATING_ZH_TO_EN.items():
        if zh in text:
            return en
    return ""


# Regex độ tin cậy: dấu hai chấm nhận cả nửa (:) lẫn toàn chiều rộng (：) — đầu ra tiếng Trung của PM hay dùng toàn chiều rộng,
# bản đầu chỉ nhận nửa chiều rộng nên không bao giờ bắt được, luôn lùi về giá trị mặc định. Phủ "置信度: 8", "置信度：8/10", "信心 7".
_CONFIDENCE_PATTERNS = [
    re.compile(r"confidence[:：\s]+(\d+(?:\.\d+)?)\s*(?:/\s*10)?", re.I),
    re.compile(r"置信度[:：\s]+(\d+(?:\.\d+)?)\s*(?:/\s*10)?", re.I),
    re.compile(r"信心(?:度)?[:：\s]+(\d+(?:\.\d+)?)\s*(?:/\s*10)?", re.I),
]

# Không bắt được con số tường minh thì suy độ tin cậy cơ sở từ thang 5 bậc (phương án B), thay vì gán cứng 5.0:
# hướng dứt khoát (买入 / 卖出) thì tin cậy cao hơn, trung tính (持有) nằm giữa.
_RATING_CONFIDENCE_FALLBACK = {
    "buy": 7.0,
    "sell": 7.0,
    "overweight": 6.0,
    "underweight": 6.0,
    "hold": 5.0,
}


def _extract_confidence(state: dict, rating_raw: str = "") -> float:
    """Độ tin cậy (0-10): ưu tiên bắt con số tường minh trong văn bản của PM/kiểm soát rủi ro/trader (phương án A);
    bắt không ra thì suy từ xếp hạng (phương án B), không còn trả về 5.0 một cách máy móc."""
    candidates = [
        state.get("final_trade_decision", ""),
        _risk_judgment(state),
        state.get("trader_investment_plan", ""),
    ]
    for text in candidates:
        if not text:
            continue
        for pat in _CONFIDENCE_PATTERNS:
            m = pat.search(text)
            if m:
                try:
                    v = float(m.group(1))
                    if v > 10:  # Quy thang 100 về thang 0-10
                        v = v / 10
                    return max(0.0, min(10.0, v))
                except (ValueError, IndexError):
                    continue
    # Dự phòng B: suy từ xếp hạng (chỉ khi không nhận ra xếp hạng nào mới lùi về 5.0)
    return _RATING_CONFIDENCE_FALLBACK.get(rating_raw, 5.0)


def _short_reason(state: dict, limit: int = 120) -> str:
    """Lấy một đoạn lý do cô đọng, ưu tiên 120 chữ đầu của final_trade_decision."""
    candidates = [
        state.get("final_trade_decision") or "",
        state.get("trader_investment_plan") or "",
        _risk_judgment(state),
    ]
    for text in candidates:
        text = text.strip()
        if text:
            return _truncate(text, limit)
    return ""


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _extract_debate(state: dict) -> dict:
    """Rút lịch sử tranh luận. investment_debate_state của thượng nguồn có cấu trúc đại khái:
    {
        "history": "...",      # toàn văn tranh luận
        "current_response": ...,
        "judge_decision": ...,
    }
    """
    debate = state.get("investment_debate_state") or {}
    if not isinstance(debate, dict):
        return {}
    return {
        "history": debate.get("history", ""),
        "current_response": debate.get("current_response", ""),
        "judge_decision": debate.get("judge_decision", ""),
    }


def _risk_judgment(state: dict) -> str:
    """Phán quyết của đội kiểm soát rủi ro: thượng nguồn đặt ở risk_debate_state.judge_decision (kết luận sau khi ba phía quyết liệt/trung lập/thận trọng tranh luận);
    thượng nguồn vốn không có trường risk_judge_decision ở cấp trên cùng, trước đây đọc nhầm nó nên phán quyết kiểm soát rủi ro luôn trống."""
    rds = state.get("risk_debate_state")
    if isinstance(rds, dict):
        jd = (rds.get("judge_decision") or "").strip()
        if jd:
            return jd
    return (state.get("risk_judge_decision") or "").strip()  # Dự phòng tương thích


def _extract_risk_debate(state: dict) -> dict:
    """Tranh luận của đội kiểm soát rủi ro (quyết liệt/trung lập/thận trọng + phán quyết), cấu trúc đối xứng với _extract_debate.
    risk_debate_state.history của thượng nguồn là toàn văn tranh luận ba phía luân phiên."""
    rds = state.get("risk_debate_state")
    if not isinstance(rds, dict):
        return {}
    return {
        "history": rds.get("history", ""),
        "judge_decision": rds.get("judge_decision", ""),
    }


def _render_notify(
    state: dict, suggestion: dict, cost_usd: float, link_md: str = ""
) -> str:
    """Thân thông báo: chỉ hiện «quyết định cuối» (tóm tắt quyết định + bản quyết định cuối của PM) + liên kết chi tiết.

    Kế hoạch thực thi của trader / phán quyết của trưởng nhóm nghiên cứu / tranh luận kiểm
    soát rủi ro / báo cáo của bốn chuyên viên phân tích đều nằm trọn ở trang chi tiết,
    không vào thông báo — vừa đúng mong muốn "thông báo chỉ xem quyết định cuối", vừa tránh
    đẩy quá dài rồi bị các kênh cắt cụt.
    """
    rating_raw = suggestion.get("rating_raw") or ""
    rating_note = (
        f"(评级:{RATING_LABEL_MAP.get(rating_raw, '持有')})"
        if rating_raw in RATING_LABEL_MAP else ""
    )
    parts = [
        f"## 最终决策\n\n"
        f"**{suggestion['action_label']}** {rating_note} · 置信度 {suggestion['confidence']:.1f}/10\n"
    ]
    final_text = (state.get("final_trade_decision") or "").strip()
    if final_text:
        parts.append(final_text + "\n")
    parts.append(
        f"\n_成本 ${cost_usd:.4f} · 交易员 / 研究主管 / 风控辩论 / 四分析师完整内容见详情_"
    )
    if link_md:
        parts.append(f"\n\n{link_md}")
    return "\n".join(parts)


def _render_markdown(
    state: dict, suggestion: dict, model_label: str, cost_usd: float
) -> str:
    parts = []

    rating_raw = suggestion.get("rating_raw") or ""
    rating_note = (
        f"(评级:{RATING_LABEL_MAP.get(rating_raw, '持有')})"
        if rating_raw in RATING_LABEL_MAP else ""
    )
    parts.append(
        f"## 最终决策\n\n"
        f"**{suggestion['action_label']}** {rating_note} · 置信度 {suggestion['confidence']:.1f}/10\n"
    )

    # Chuỗi 9 Agent: PM (bản quyết định) → Trader → trưởng bộ phận nghiên cứu → quản trị rủi ro → tóm tắt của 4 chuyên viên phân tích
    if state.get("final_trade_decision"):
        parts.append(f"### 🎯 PM 最终决策书\n\n{state['final_trade_decision']}\n")

    if state.get("trader_investment_plan"):
        parts.append(f"### 💼 交易员执行计划\n\n{state['trader_investment_plan']}\n")

    # Phán quyết của trưởng bộ phận nghiên cứu — kết luận sau tranh luận mua / bán, trước đây chỉ nằm ở cuối mục tranh luận bị thu gọn
    debate = state.get("investment_debate_state") or {}
    judge_decision = ""
    if isinstance(debate, dict):
        judge_decision = (debate.get("judge_decision") or "").strip()
    if judge_decision:
        parts.append(f"### ⚖️ 研究主管裁决(看多 vs 看空)\n\n{judge_decision}\n")

    risk_jd = _risk_judgment(state)
    if risk_jd:
        parts.append(f"### 🛡️ 风控辩论裁决\n\n{risk_jd}\n")

    # Báo cáo đầy đủ của 4 chuyên viên không nhét vào markdown thân nữa (bản trước cắt 300 chữ khiến bảng tài chính bị cắt ngay ở dòng tiêu đề).
    # Nội dung đầy đủ nằm ở raw_data.analyst_reports, giao diện dựng đủ trong tab (kể cả bảng GFM).

    parts.append(
        "\n---\n"
        f"_本分析由 TradingAgents 9-Agent 框架生成(技术/情绪/新闻/基本面 → 看多看空辩论 "
        f"→ 研究主管 → 交易员 → 风控辩论 → PM)。仅供学习研究参考,不构成投资建议。_\n"
        f"\n成本:${cost_usd:.4f}"
    )
    if model_label:
        parts.append(f" · AI:{model_label}")

    return "\n".join(parts)


# ============================================================================
# Paper trading bridge
# ============================================================================

logger = logging.getLogger(__name__)


def maybe_emit_paper_trading_signal(
    *,
    stock_symbol: str,
    stock_market: str,
    stock_name: str,
    decision: str,
    confidence: float,
    signal_text: str,
    reason: str,
    current_price: float | None,
    enabled: bool,
) -> bool:
    """Ghi quyết định TA vào StrategySignalRun. Trả về có thực sự ghi hay không.

    - Chỉ ghi khi enabled=True và decision thuộc (buy, add) (SELL không mở vị thế mới)
    - entry_low/high lấy giá hiện tại ±2% làm vùng vào lệnh
    - stop_loss lấy giá vào lệnh -5%, target_price +10% (thô, Phase C có thể để TA xuất chính xác hơn)
    - Gộp trùng cùng mã cùng ngày: tính duy nhất theo strategy_code+source_candidate_id
    """
    if not enabled:
        return False
    action = (decision or "").lower()
    if action not in ("buy", "add"):
        return False
    if not current_price or current_price <= 0:
        logger.warning(
            f"[TA paper] {stock_symbol} 当前价缺失,跳过写信号"
        )
        return False

    from src.platform.persistence.database import SessionLocal
    from src.platform.persistence.models import StrategySignalRun

    snapshot_date = date.today().isoformat()
    entry_low = round(current_price * 0.98, 2)
    entry_high = round(current_price * 1.02, 2)
    stop_loss = round(current_price * 0.95, 3)
    target_price = round(current_price * 1.10, 3)

    db = SessionLocal()
    try:
        # Cùng mã kích hoạt lại trong ngày → upsert (source_candidate_id kiểu Integer, dùng 0 làm giá trị canh riêng cho TA)
        source_id = 0
        existing = (
            db.query(StrategySignalRun)
            .filter(
                StrategySignalRun.snapshot_date == snapshot_date,
                StrategySignalRun.stock_symbol == stock_symbol,
                StrategySignalRun.stock_market == stock_market,
                StrategySignalRun.strategy_code == "tradingagents",
                StrategySignalRun.source_candidate_id == source_id,
            )
            .first()
        )
        if existing:
            existing.action = action
            existing.action_label = DECISION_LABEL_MAP.get(action, "买入")
            existing.signal = signal_text[:500]
            existing.reason = reason[:1000]
            existing.confidence = confidence
            existing.entry_low = entry_low
            existing.entry_high = entry_high
            existing.stop_loss = stop_loss
            existing.target_price = target_price
            existing.status = "active"
        else:
            row = StrategySignalRun(
                snapshot_date=snapshot_date,
                stock_symbol=stock_symbol,
                stock_market=stock_market,
                stock_name=stock_name or stock_symbol,
                strategy_code="tradingagents",
                strategy_name="TradingAgents 深度分析",
                strategy_version="v1",
                risk_level="medium",
                source_pool="watchlist",
                score=float(confidence or 5.0),
                rank_score=float(confidence or 5.0) * 10,  # Gán một mức điểm thiên về trung bình
                confidence=float(confidence or 5.0) / 10,
                status="active",
                action=action,
                action_label=DECISION_LABEL_MAP.get(action, "买入"),
                signal=signal_text[:500],
                reason=reason[:1000],
                evidence=[],
                holding_days=10,  # time_horizon mà TA đưa ra là trung - dài hạn
                entry_low=entry_low,
                entry_high=entry_high,
                stop_loss=stop_loss,
                target_price=target_price,
                invalidation="价格跌破止损位 / 基本面恶化",
                plan_quality=70,
                source_agent="tradingagents",
                source_candidate_id=source_id,
            )
            db.add(row)
        db.commit()
        logger.info(
            f"[TA paper] 已写信号: {stock_symbol} {action} "
            f"entry=[{entry_low}, {entry_high}] stop={stop_loss} target={target_price}"
        )
        return True
    except Exception as e:
        logger.warning(f"[TA paper] 写 StrategySignalRun 失败: {e}")
        db.rollback()
        return False
    finally:
        db.close()
