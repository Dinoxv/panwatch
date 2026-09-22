"""Phần tính toán thuần của việc ôn lại khuyến nghị Agent.

Ở đây giữ tập trung định nghĩa "thế nào là trúng" và cách gộp bản ghi horizon, để API
lẫn frontend đều không tự chép lại quy tắc.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


FLAT_THRESHOLD_PCT = 2.0

EVALUATION_POLICY = {
    "horizon_unit": "trading_days",
    "flat_threshold_pct": FLAT_THRESHOLD_PCT,
    "actions": {
        "buy": "后续收益大于 0 记为命中",
        "add": "后续收益大于 0 记为命中",
        "sell": "后续收益小于 0 记为命中",
        "reduce": "后续收益小于 0 记为命中",
        "avoid": "后续收益小于 0 记为命中",
        "hold": "后续绝对收益小于 2% 记为命中",
        "watch": "后续绝对收益小于 2% 记为命中",
    },
}

_UP_ACTIONS = {"buy", "add"}
_DOWN_ACTIONS = {"sell", "reduce", "avoid"}
_FLAT_ACTIONS = {"hold", "watch"}


def classify_prediction_hit(action: str, return_pct: float | None) -> bool | None:
    """Xét theo chính sách công khai xem hướng khuyến nghị có trúng không; không xét được thì trả None."""
    if return_pct is None:
        return None
    try:
        value = float(return_pct)
    except (TypeError, ValueError):
        return None

    normalized = (action or "").strip().lower()
    if normalized in _UP_ACTIONS:
        return value > 0
    if normalized in _DOWN_ACTIONS:
        return value < 0
    if normalized in _FLAT_ACTIONS:
        return abs(value) < FLAT_THRESHOLD_PCT
    return None


def _legacy_group_base_key(row: Any) -> str:
    return ":".join(
        [
            str(getattr(row, "agent_name", "")),
            str(getattr(row, "stock_market", "")),
            str(getattr(row, "stock_symbol", "")),
            str(getattr(row, "prediction_date", "")),
            str(getattr(row, "action", "")),
        ]
    )


def _legacy_group_ids(rows: Sequence[Any]) -> dict[int, str]:
    """Ghép cặp horizon cho các dòng lịch sử không có ID nhóm lưu bền, theo thứ tự ghi cũ.

    Bản cũ gửi lần lượt từng bản 1/5 ngày, thời điểm tạo có thể trùng nhau mà cũng có thể
    lệch giây, nên không dùng làm danh tính được. Ở đây dựa vào thứ tự ghi của ID tự tăng
    trong cơ sở dữ liệu để gom các horizon của cùng một khuyến nghị vào một nhóm tạm.
    """
    result: dict[int, str] = {}

    def sort_key(entry: tuple[int, Any]) -> tuple[int, int]:
        index, row = entry
        try:
            return int(getattr(row, "id", 0) or 0), index
        except (TypeError, ValueError):
            return 0, index

    previous: dict[str, Any] | None = None
    for index, row in sorted(enumerate(rows), key=sort_key):
        if getattr(row, "prediction_group_id", None):
            previous = None
            continue

        base_key = _legacy_group_base_key(row)
        horizon = str(max(1, int(getattr(row, "horizon_days", 1) or 1)))
        record_id, _ = sort_key((index, row))
        can_extend = (
            previous is not None
            and previous["base_key"] == base_key
            and record_id == previous["last_id"] + 1
            and horizon not in previous["horizons"]
            and not previous["ambiguous"]
        )
        if not can_extend:
            same_base_as_previous = (
                previous is not None and previous["base_key"] == base_key
            )
            previous = {
                "base_key": base_key,
                "key": f"legacy:{base_key}:{record_id or index}",
                "horizons": set(),
                "last_id": record_id,
                # Khi cùng một khóa chưa đủ ít nhất hai horizon mà đã có bản ghi mới, không thể biết
                # kết quả sau thuộc về lần khuyến nghị nào; thà không ghép cặp còn hơn để kết quả nhiễm chéo.
                "ambiguous": bool(
                    same_base_as_previous
                    and (previous["ambiguous"] or len(previous["horizons"]) < 2)
                ),
            }
        previous["horizons"].add(horizon)
        previous["last_id"] = record_id
        result[index] = previous["key"]
    return result


def _serialize_timestamp(value: Any) -> str:
    if value is None:
        return ""
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _outcome_payload(row: Any) -> dict[str, Any]:
    return_pct = getattr(row, "outcome_return_pct", None)
    status = str(getattr(row, "outcome_status", "pending") or "pending")
    action = str(getattr(row, "action", "") or "")
    unit = str(getattr(row, "horizon_unit", "calendar_days_legacy") or "calendar_days_legacy")
    return {
        "status": status,
        "horizon_unit": unit,
        "outcome_price": getattr(row, "outcome_price", None),
        "return_pct": return_pct,
        "hit": classify_prediction_hit(action, return_pct)
        if status == "evaluated"
        else None,
        "evaluated_at": _serialize_timestamp(getattr(row, "evaluated_at", None)),
    }


def group_prediction_outcomes(rows: Sequence[Any]) -> list[dict[str, Any]]:
    """Pivot nhiều bản ghi horizon của một khuyến nghị thành một dòng dữ liệu ôn lại."""
    grouped: dict[str, dict[str, Any]] = {}
    legacy_group_ids = _legacy_group_ids(rows)

    for index, row in enumerate(rows):
        group_id = str(
            getattr(row, "prediction_group_id", "") or legacy_group_ids[index]
        )
        group = grouped.get(group_id)
        if group is None:
            meta = getattr(row, "meta", None) or {}
            group = {
                "prediction_group_id": group_id,
                "is_legacy_group": not bool(getattr(row, "prediction_group_id", None)),
                "agent_name": str(getattr(row, "agent_name", "") or ""),
                "stock_symbol": str(getattr(row, "stock_symbol", "") or ""),
                "stock_market": str(getattr(row, "stock_market", "") or ""),
                "prediction_date": str(getattr(row, "prediction_date", "") or ""),
                "action": str(getattr(row, "action", "") or ""),
                "action_label": str(getattr(row, "action_label", "") or ""),
                "confidence": getattr(row, "confidence", None),
                "trigger_price": getattr(row, "trigger_price", None),
                "reason": str(meta.get("reason", "") or ""),
                "signal": str(meta.get("signal", "") or ""),
                "created_at": _serialize_timestamp(getattr(row, "created_at", None)),
                "outcomes": {},
            }
            grouped[group_id] = group

        horizon = str(max(1, int(getattr(row, "horizon_days", 1) or 1)))
        group["outcomes"][horizon] = _outcome_payload(row)

    return sorted(
        grouped.values(),
        key=lambda item: (item["prediction_date"], item["prediction_group_id"]),
        reverse=True,
    )


def summarize_prediction_groups(groups: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Thống kê mức phủ, mức trúng và lợi nhuận theo khẩu độ phiên giao dịch mặc định, mẫu chưa đủ thì đánh dấu rõ."""
    horizon_stats: dict[str, dict[str, Any]] = {}
    pending_count = 0

    for group in groups:
        for horizon, outcome in (group.get("outcomes") or {}).items():
            if outcome.get("status") == "pending":
                pending_count += 1
            if outcome.get("horizon_unit") != "trading_days":
                continue
            stat = horizon_stats.setdefault(
                str(horizon),
                {
                    "completed_count": 0,
                    "hit_count": 0,
                    "hit_rate": None,
                    "avg_return_pct": None,
                },
            )
            if outcome.get("status") != "evaluated" or outcome.get("hit") is None:
                continue
            stat["completed_count"] += 1
            stat["hit_count"] += int(bool(outcome["hit"]))
            current_total = stat.get("_return_total", 0.0)
            stat["_return_total"] = current_total + float(outcome["return_pct"])

    for stat in horizon_stats.values():
        completed = stat["completed_count"]
        if completed:
            stat["hit_rate"] = round(stat["hit_count"] / completed, 4)
            stat["avg_return_pct"] = round(stat.pop("_return_total") / completed, 4)
        else:
            stat.pop("_return_total", None)

    completed_5d = horizon_stats.get("5", {}).get("completed_count", 0)
    return {
        "suggestion_count": len(groups),
        "pending_count": pending_count,
        "horizons": horizon_stats,
        "insufficient_sample": completed_5d < 20,
        "policy": EVALUATION_POLICY,
    }
