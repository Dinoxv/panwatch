"""API trọng số nhân tố (M5): danh sách chỉ đọc + ghi đè tay (pin / đặt trọng số / bật tắt tự chuẩn định).

Phản hồi do ResponseWrapperMiddleware bọc thống nhất thành {code,data,message}, route trả thẳng dữ liệu gốc.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.modules.strategy.factor_weights import get_all_factor_weights, set_factor_weight
from src.platform.persistence.database import get_db

router = APIRouter()


class FactorWeightUpdate(BaseModel):
    """Tham số ghi đè tay, đều tùy chọn (chỉ truyền trường muốn đổi)."""

    weight: float | None = None
    is_pinned: bool | None = None
    auto_calibrate: bool | None = None


@router.get("/weights")
def list_weights(db: Session = Depends(get_db)):
    """Liệt kê trọng số của mọi thị trường × nhân tố + quan sát IC/IR gần nhất."""
    return {"items": get_all_factor_weights(db=db)}


@router.post("/weights/{factor_code}/{market}")
def update_weight(
    factor_code: str, market: str, payload: FactorWeightUpdate,
    db: Session = Depends(get_db),
):
    """Ghi đè tay trọng số của một nhân tố / pin / bật tắt tự chuẩn định (trọng số đổi thì ghi kiểm toán manual)."""
    try:
        return set_factor_weight(
            factor_code, market,
            weight=payload.weight, is_pinned=payload.is_pinned,
            auto_calibrate=payload.auto_calibrate, db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
