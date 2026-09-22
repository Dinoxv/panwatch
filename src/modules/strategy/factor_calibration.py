"""Tự chuẩn định nhân tố (M2): nối phần IC/IR vốn đứng riêng vào vòng chuẩn định nhẹ cho trọng số nhân tố.

Lấy IC/IR của từng nhân tố mà `factor_eval.evaluate_factor_ic` tính ra, đổi thành trọng số
mục tiêu có xét dấu, làm mượt bằng EMA + clamp rồi ghi vào `FactorWeight` (và ghi kiểm
toán vào `FactorWeightHistory`).
Soi gương cơ chế của `strategy_engine.rebalance_strategy_weights`, nhưng tác dụng ở cấp nhân tố.

Điểm thiết kế (xem .docs/factor-self-calibration-design-2026-06-20.md):
- IC bắt buộc đo trên «nhân tố thô» (ảnh chụp lưu raw, trọng số chỉ nhân lúc tổng hợp), nếu không vòng lặp sẽ tự củng cố mà méo đi.
- Nhân tố phạt (risk/crowd) có IC kỳ vọng âm: lấy −IC làm động lực, phạt hiệu quả → tăng trọng số, mất hiệu lực → giảm trọng số.
- Chỉ ăn outcome «đã đi hết kỳ nắm giữ» (point-in-time), chống hàm nhìn trước tương lai.
"""

from __future__ import annotations

import logging

from src.modules.strategy.factor_eval import evaluate_factor_ic
from src.modules.strategy.factor_weights import (
    CALIBRATABLE_FACTORS,
    MARKETS,
    PENALTY_FACTORS,
    get_factor_weights,
)
from src.platform.scheduling.timezone import utc_now
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import FactorWeight, FactorWeightHistory

logger = logging.getLogger(__name__)

# Mốc chuẩn hóa: một IR ở mức «khá» / một IC một kỳ ở mức «có ý nghĩa».
IR_REF = 0.5
IC_REF = 0.05


def compute_target(factor_code: str, ic, ir, *, beta: float = 0.4) -> float | None:
    """Tính trọng số mục tiêu từ IC/IR; ưu tiên IR (ổn hơn), lùi về IC; nhân tố phạt thì đảo dấu.

    Trả None nghĩa là thiếu thông tin (thiếu cả IC lẫn IR), nên bỏ qua nhân tố đó.
    term được chuẩn hóa và clamp về [-1, 1]; target = 1 + beta·term.
    """
    if ir is not None:
        term = ir / IR_REF
    elif ic is not None:
        term = ic / IC_REF
    else:
        return None
    if factor_code in PENALTY_FACTORS:
        term = -term  # Nhân tố phạt: IC càng âm thì càng đáng tin
    term = max(-1.0, min(1.0, term))
    return 1.0 * (1.0 + beta * term)


def blend(old: float, target: float, *, alpha: float = 0.35,
          lo: float = 0.5, hi: float = 1.5) -> float:
    """Làm mượt bằng EMA (chống nhảy đột ngột) + clamp về [lo, hi]."""
    new = old * (1.0 - alpha) + target * alpha
    return max(lo, min(hi, new))


def calibrate_factor_weights(
    market: str, *, alpha: float = 0.35, beta: float = 0.4,
    clamp: tuple[float, float] = (0.5, 1.5),
    min_samples: int = 20, horizon: int = 5, days: int = 90, db=None,
) -> dict:
    """Chạy một vòng chuẩn định trọng số nhân tố cho một thị trường, ghi FactorWeight + FactorWeightHistory.

    Cổng chặn: is_pinned / auto_calibrate=False / mẫu chưa đủ / thiếu IC → bỏ qua (không đổi trọng số).
    Lần nào cũng ghi quan sát gần nhất (last_ic/ir/sample_size) vào FactorWeight.meta cho API hiển thị;
    History chỉ ghi «điều chỉnh đã thực sự xảy ra» (reason=auto), tránh nhiễu kiểm toán trong giai đoạn khởi động nguội.
    """
    own = db is None
    db = db or SessionLocal()
    try:
        ic_result = evaluate_factor_ic(
            days=days, horizon=horizon, min_samples=min_samples, market=market, db=db
        )
        factors = ic_result.get("factors", {})
        get_factor_weights(market, db=db)  # Bảo đảm đủ 5 dòng nhân tố

        lo, hi = float(clamp[0]), float(clamp[1])
        changed = 0
        rows_changed: list[dict] = []

        for code in CALIBRATABLE_FACTORS:
            row = (
                db.query(FactorWeight)
                .filter(FactorWeight.factor_code == code, FactorWeight.market == market)
                .first()
            )
            old = float(row.weight)
            stats = factors.get(code, {})
            ic = stats.get("ic")
            ir = stats.get("ir")
            n = int(stats.get("sample_size", 0))

            # Ghi lại quan sát gần nhất (để API hiển thị), bất kể có điều chỉnh hay không.
            row.meta = {
                **(row.meta or {}),
                "last_ic": ic, "last_ir": ir, "last_sample_size": n,
                "last_calibrated_at": utc_now().isoformat(),
            }

            if row.is_pinned or not row.auto_calibrate:
                continue
            if n < min_samples or ic is None:
                continue
            target = compute_target(code, ic, ir, beta=beta)
            if target is None:
                continue
            new = round(blend(old, target, alpha=alpha, lo=lo, hi=hi), 4)
            if abs(new - old) < 0.01:
                continue

            row.weight = new
            row.reason = f"auto(ic={ic}, ir={ir}, n={n})"
            row.effective_from = utc_now()
            row.updated_at = utc_now()
            db.add(FactorWeightHistory(
                factor_code=code, market=market, old_weight=old, new_weight=new,
                ic=ic, ir=ir, sample_size=n, reason="auto",
                meta={"target": round(target, 4), "alpha": alpha},
            ))
            changed += 1
            rows_changed.append({
                "factor_code": code, "old_weight": old, "new_weight": new, "sample_size": n,
            })

        db.commit()
        return {"market": market, "checked": len(CALIBRATABLE_FACTORS),
                "changed": changed, "rows": rows_changed}
    except Exception as e:  # pragma: no cover - nhánh phòng thủ
        logger.warning(f"[因子标定] market={market} 失败: {e}")
        db.rollback()
        return {"market": market, "checked": 0, "changed": 0, "rows": [], "error": str(e)}
    finally:
        if own:
            db.close()


def calibrate_all_markets(*, db=None, **kwargs) -> dict[str, dict]:
    """Chạy một vòng chuẩn định nhân tố cho mọi thị trường (CN/HK/US); cho bộ lập lịch gọi sau khi hậu kiểm outcome hằng ngày.

    kwargs chuyển thẳng cho calibrate_factor_weights (alpha/beta/clamp/min_samples/horizon/days).
    """
    own = db is None
    db = db or SessionLocal()
    try:
        return {m: calibrate_factor_weights(m, db=db, **kwargs) for m in MARKETS}
    finally:
        if own:
            db.close()
