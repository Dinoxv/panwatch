"""Tầng đọc ghi trọng số nhân tố (M1).

Đổi trọng số của từng nhân tố lúc tổng hợp tín hiệu từ «ngầm định = 1 ghi cứng» thành
«để ngoài + chuẩn định được»:
- `get_factor_weights(market)`: đọc trọng số từng nhân tố của một thị trường, thiếu thì lazy seed bằng 1.0;
  cho `strategy_engine._compute_factor_breakdown` nhân theo nhân tố lúc tổng hợp raw_score.

Phần chuẩn định xem `factor_calibration.py`; API chỉ đọc/ghi đè tay nằm ở `web/api/factors.py`.
"""

from __future__ import annotations

import logging

from src.platform.scheduling.timezone import utc_now
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import FactorWeight, FactorWeightHistory

logger = logging.getLogger(__name__)

# Các nhân tố hiệu chỉnh được (khớp với cột của StrategyFactorSnapshot và factor_eval.FACTOR_FIELDS).
# source_bonus tạm chưa vào tập hiệu chỉnh (v1 giữ trọng số 1.0); final_score là kết quả tổng hợp chứ không phải nhân tố đầu vào.
CALIBRATABLE_FACTORS = (
    "alpha_score",
    "catalyst_score",
    "quality_score",
    "risk_penalty",
    "crowd_penalty",
)

# Nhân tố phạt: bị trừ trong raw_score, kỳ vọng IC âm.
PENALTY_FACTORS = frozenset({"risk_penalty", "crowd_penalty"})

MARKETS = ("CN", "HK", "US")


def get_factor_weights(market: str, *, db=None) -> dict[str, float]:
    """Đọc trọng số của các nhân tố chuẩn định được trong một thị trường; nhân tố thiếu thì lazy seed bằng 1.0.

    Trả về {factor_code: weight}, khóa luôn là trọn bộ CALIBRATABLE_FACTORS.
    Bên tiêu thụ nên dùng `.get(code, 1.0)` để hứng cho nhân tố chưa đăng ký.
    """
    own = db is None
    db = db or SessionLocal()
    try:
        rows = db.query(FactorWeight).filter(FactorWeight.market == market).all()
        existing = {r.factor_code: float(r.weight) for r in rows}
        missing = [f for f in CALIBRATABLE_FACTORS if f not in existing]
        if missing:
            for f in missing:
                db.add(FactorWeight(factor_code=f, market=market, weight=1.0))
            db.commit()
            for f in missing:
                existing[f] = 1.0
        return {f: existing.get(f, 1.0) for f in CALIBRATABLE_FACTORS}
    finally:
        if own:
            db.close()


def _serialize(row: FactorWeight) -> dict:
    meta = row.meta or {}
    return {
        "factor_code": row.factor_code,
        "market": row.market,
        "weight": round(float(row.weight), 4),
        "is_pinned": bool(row.is_pinned),
        "auto_calibrate": bool(row.auto_calibrate),
        "last_ic": meta.get("last_ic"),
        "last_ir": meta.get("last_ir"),
        "last_sample_size": meta.get("last_sample_size"),
        "last_calibrated_at": meta.get("last_calibrated_at"),
        "reason": row.reason or "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_all_factor_weights(*, db=None) -> list[dict]:
    """Liệt kê trọng số của mọi thị trường × nhân tố (kèm quan sát IC/IR gần nhất), cho API/giao diện chỉ đọc hiển thị."""
    own = db is None
    db = db or SessionLocal()
    try:
        for m in MARKETS:
            get_factor_weights(m, db=db)  # Bảo đảm mọi thị trường đã được khởi tạo dữ liệu nền
        rows = (
            db.query(FactorWeight)
            .order_by(FactorWeight.market, FactorWeight.factor_code)
            .all()
        )
        return [_serialize(r) for r in rows]
    finally:
        if own:
            db.close()


def set_factor_weight(
    factor_code: str, market: str, *,
    weight: float | None = None, is_pinned: bool | None = None,
    auto_calibrate: bool | None = None, db=None,
) -> dict:
    """Ghi đè tay trọng số của một nhân tố / pin / bật tắt tự chuẩn định; trọng số đổi thì ghi kiểm toán manual."""
    if factor_code not in CALIBRATABLE_FACTORS:
        raise ValueError(f"未知因子: {factor_code}")
    if market not in MARKETS:
        raise ValueError(f"未知市场: {market}")
    own = db is None
    db = db or SessionLocal()
    try:
        get_factor_weights(market, db=db)  # Bảo đảm dòng dữ liệu tồn tại
        row = (
            db.query(FactorWeight)
            .filter(FactorWeight.factor_code == factor_code, FactorWeight.market == market)
            .first()
        )
        if weight is not None:
            old = float(row.weight)
            new = round(max(0.1, min(3.0, float(weight))), 4)  # Nhập tay cũng có chặn biên, phòng điền nhầm
            if abs(new - old) >= 1e-9:
                row.weight = new
                row.reason = "manual"
                row.effective_from = utc_now()
                row.updated_at = utc_now()
                db.add(FactorWeightHistory(
                    factor_code=factor_code, market=market,
                    old_weight=old, new_weight=new, sample_size=0, reason="manual",
                ))
        if is_pinned is not None:
            row.is_pinned = bool(is_pinned)
            row.updated_at = utc_now()
        if auto_calibrate is not None:
            row.auto_calibrate = bool(auto_calibrate)
            row.updated_at = utc_now()
        db.commit()
        return _serialize(row)
    finally:
        if own:
            db.close()
