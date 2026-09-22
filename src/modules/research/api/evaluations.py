"""API trung tâm kiểm chứng: phần ôn lại khuyến nghị của Agent dành cho sản phẩm."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.modules.automation.agent_prediction_evaluation import (
    EVALUATION_POLICY,
    group_prediction_outcomes,
    summarize_prediction_groups,
)
from src.modules.research.prediction_outcome import evaluate_pending_prediction_outcomes
from src.platform.persistence.database import get_db
from src.platform.persistence.models import AgentPredictionOutcome


router = APIRouter()


def query_prediction_rows(
    *,
    db: Session,
    agent_name: str | None = None,
    market: str | None = None,
    action: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    horizon_unit: str | None = "trading_days",
    days: int = 90,
) -> list[AgentPredictionOutcome]:
    """Nạp một nhóm bản ghi horizon thô, phần gộp và phân trang để bên gọi làm."""
    cutoff = (date.today() - timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d")
    query = db.query(AgentPredictionOutcome).filter(
        AgentPredictionOutcome.prediction_date >= cutoff
    )
    if agent_name:
        query = query.filter(AgentPredictionOutcome.agent_name == agent_name)
    if market:
        query = query.filter(AgentPredictionOutcome.stock_market == market.upper())
    if action:
        query = query.filter(AgentPredictionOutcome.action == action.lower())
    if start_date:
        query = query.filter(AgentPredictionOutcome.prediction_date >= start_date)
    if end_date:
        query = query.filter(AgentPredictionOutcome.prediction_date <= end_date)
    if horizon_unit and horizon_unit != "all":
        query = query.filter(AgentPredictionOutcome.horizon_unit == horizon_unit)
    return query.order_by(
        AgentPredictionOutcome.prediction_date.desc(),
        AgentPredictionOutcome.created_at.desc(),
    ).all()


def filter_prediction_groups_by_status(
    groups: list[dict], status: str | None
) -> list[dict]:
    """Lọc trạng thái theo nhóm khuyến nghị, luôn giữ trọn mọi kết quả horizon của nhóm đó."""
    if not status:
        return groups
    return [
        group
        for group in groups
        if any(
            outcome.get("status") == status
            for outcome in (group.get("outcomes") or {}).values()
        )
    ]


def _available_filters(rows: list[AgentPredictionOutcome]) -> dict[str, list[str]]:
    return {
        "agent_names": sorted({row.agent_name for row in rows if row.agent_name}),
        "markets": sorted({row.stock_market for row in rows if row.stock_market}),
        "actions": sorted({row.action for row in rows if row.action}),
        "statuses": sorted({row.outcome_status for row in rows if row.outcome_status}),
        "horizon_units": sorted({row.horizon_unit for row in rows if row.horizon_unit}),
    }


@router.get("/agent-predictions")
def list_agent_predictions(
    agent_name: str | None = None,
    market: str | None = None,
    action: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    horizon_unit: Annotated[str | None, Query()] = "trading_days",
    days: int = Query(default=90, ge=1, le=720),
    limit: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0, le=3000),
    db: Session = Depends(get_db),
):
    """Trả về các dòng ôn lại theo nhóm khuyến nghị; mặc định chỉ hiện khẩu độ phiên giao dịch."""
    rows = query_prediction_rows(
        db=db,
        agent_name=agent_name,
        market=market,
        action=action,
        status=status,
        start_date=start_date,
        end_date=end_date,
        horizon_unit=horizon_unit,
        days=days,
    )
    groups = filter_prediction_groups_by_status(
        group_prediction_outcomes(rows), status
    )
    return {
        "items": groups[offset : offset + limit],
        "total": len(groups),
        "available_filters": _available_filters(rows),
        "policy": EVALUATION_POLICY,
    }


@router.get("/agent-predictions/summary")
def get_agent_prediction_summary(
    agent_name: str | None = None,
    market: str | None = None,
    action: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    horizon_unit: Annotated[str | None, Query()] = "trading_days",
    days: int = Query(default=90, ge=1, le=720),
    db: Session = Depends(get_db),
):
    """Trả về tổng hợp mức trúng, mức phủ và cỡ mẫu khớp với bộ lọc của danh sách."""
    rows = query_prediction_rows(
        db=db,
        agent_name=agent_name,
        market=market,
        action=action,
        status=status,
        start_date=start_date,
        end_date=end_date,
        horizon_unit=horizon_unit,
        days=days,
    )
    groups = filter_prediction_groups_by_status(
        group_prediction_outcomes(rows), status
    )
    return summarize_prediction_groups(groups)


@router.post("/agent-predictions/evaluate")
def evaluate_agent_predictions(
    max_horizon_days: int = Query(default=5, ge=1, le=5),
    limit: int = Query(default=300, ge=1, le=300),
):
    """Kiểm tay các khuyến nghị đã tới hạn; bản ghi chưa tới hạn giữ nguyên pending."""
    return evaluate_pending_prediction_outcomes(
        max_horizon_days=max_horizon_days,
        limit=limit,
    )
