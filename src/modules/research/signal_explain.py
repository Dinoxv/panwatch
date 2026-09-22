"""Giải nghĩa tín hiệu (Phase 3): rank_score → điểm AI 1-10 + bóc tách yếu tố thuận lợi/bất lợi.

Đối chiếu với điểm AI 1-10 và AI Factors xanh/đỏ của Danelfin: lấy score_breakdown mà
strategy_engine đã tính (các mục cộng điểm alpha/catalyst/quality/source_bonus, các mục
trừ điểm risk/crowd penalty) rồi tách thành hai nhóm «thuận lợi (xanh, nâng điểm) / bất
lợi (đỏ, kéo điểm)», để trang cơ hội hiển thị.

Hàm thuần, không phụ thuộc DB; tiêm vào ở tầng API bằng cách hậu xử lý kết quả của
list_strategy_signals.
"""

from __future__ import annotations

# Nhãn hiển thị của nhân tố
FACTOR_LABELS = {
    "alpha_score": "选股α",
    "catalyst_score": "催化",
    "quality_score": "计划质量",
    "source_bonus": "来源加成",
    "risk_penalty": "风险",
    "crowd_penalty": "拥挤度",
}

# Nhân tố cộng điểm (dương = nâng đỡ, âm = kéo lùi)
ADDITIVE_FACTORS = ("alpha_score", "catalyst_score", "quality_score", "source_bonus")
# Nhân tố phạt (dương = kéo lùi, trong score_breakdown dùng số dương để biểu thị cường độ phạt)
PENALTY_FACTORS = ("risk_penalty", "crowd_penalty")

_EPS = 0.01


def to_ai_score(rank_score) -> int:
    """rank_score (0-100) → điểm AI 1-10 (kẹp về [1,10])."""
    try:
        s = float(rank_score or 0.0)
    except (TypeError, ValueError):
        s = 0.0
    return max(1, min(10, round(s / 10.0)))


def explain_factors(score_breakdown) -> dict:
    """Tách thành hai nhóm thuận lợi (xanh)/bất lợi (đỏ), mỗi nhóm xếp theo trị tuyệt đối mức đóng góp rồi lấy 5 mục đầu."""
    sb = score_breakdown if isinstance(score_breakdown, dict) else {}
    positive: list[dict] = []
    negative: list[dict] = []

    def _f(key):
        try:
            return float(sb.get(key))
        except (TypeError, ValueError):
            return None

    for key in ADDITIVE_FACTORS:
        v = _f(key)
        if v is None:
            continue
        if v > _EPS:
            positive.append({"factor": key, "label": FACTOR_LABELS.get(key, key), "contribution": round(v, 2)})
        elif v < -_EPS:
            negative.append({"factor": key, "label": FACTOR_LABELS.get(key, key), "contribution": round(v, 2)})

    for key in PENALTY_FACTORS:
        v = _f(key)
        if v is None:
            continue
        if v > _EPS:  # Phạt dương = kéo lùi, phần đóng góp ghi là âm
            negative.append({"factor": key, "label": FACTOR_LABELS.get(key, key), "contribution": round(-v, 2)})

    positive.sort(key=lambda x: x["contribution"], reverse=True)
    negative.sort(key=lambda x: x["contribution"])  # Âm nhất xếp trước
    return {"positive": positive[:5], "negative": negative[:5]}


def enrich_signal(item: dict) -> dict:
    """Tiêm ai_score + factor_explain vào một item tín hiệu (sửa tại chỗ rồi trả về)."""
    if not isinstance(item, dict):
        return item
    item["ai_score"] = to_ai_score(item.get("rank_score"))
    item["factor_explain"] = explain_factors(item.get("score_breakdown"))
    return item
