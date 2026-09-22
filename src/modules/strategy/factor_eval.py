"""Đánh giá hiệu lực nhân tố (Phase 2): IC / IR.

Trả lời câu hỏi «nhân tố nào thật sự có alpha ở cổ phiếu A» — nối
StrategyFactorSnapshot (điểm nhân tố của từng tín hiệu) với StrategyOutcome (lợi nhuận
tiến về trước) theo signal_run_id, rồi tính cho mỗi nhân tố:
- IC (hệ số thông tin): tương quan hạng Spearman giữa giá trị nhân tố và lợi nhuận tương lai (toàn mẫu)
- IR (tỷ lệ thông tin): mean/std của chuỗi IC gom theo ngày ảnh chụp

Hệ số tương quan cài đặt bằng Python thuần (không kéo scipy/alphalens), cùng ràng buộc
nhẹ như lõi kiểm thử lịch sử.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from statistics import fmean, stdev

from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import StrategyFactorSnapshot, StrategyOutcome

logger = logging.getLogger(__name__)

# Các trường nhân tố tham gia đánh giá (ứng với cột của StrategyFactorSnapshot)
FACTOR_FIELDS = (
    "alpha_score",
    "catalyst_score",
    "quality_score",
    "risk_penalty",   # Thành phần phạt, kỳ vọng IC âm
    "crowd_penalty",  # Thành phần phạt, kỳ vọng IC âm
    "final_score",
)


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Hệ số tương quan tuyến tính Pearson; mẫu < 3 hoặc phương sai bằng 0 thì trả None."""
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx ** 0.5 * vy ** 0.5)


def _rankdata(values: list[float]) -> list[float]:
    """Hạng bình quân (bắt đầu từ 1; đồng hạng thì lấy trung bình)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Tương quan hạng Spearman = chạy Pearson trên hạng."""
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    return pearson(_rankdata(xs), _rankdata(ys))


def evaluate_factor_ic(
    *, days: int = 90, horizon: int = 5, min_samples: int = 20, min_period_samples: int = 5,
    market: str | None = None, db=None,
) -> dict:
    """Tính IC/IR của từng nhân tố.

    Args:
        days: nhìn lại bao nhiêu ngày ảnh chụp
        horizon: dùng outcome của kỳ nắm giữ nào (phiên giao dịch)
        min_samples: cỡ mẫu tối thiểu cho IC toàn mẫu
        min_period_samples: cỡ mẫu tối thiểu cho IC một ngày (dùng cho chuỗi thời gian của IR)
    """
    own = db is None
    db = db or SessionLocal()
    try:
        cutoff = (date.today() - timedelta(days=max(7, int(days)))).strftime("%Y-%m-%d")
        # Chống rò rỉ dữ liệu tương lai (point-in-time): chỉ lấy các mẫu đã đi hết kỳ nắm giữ
        # (snapshot_date + horizon ngày lịch <= hôm nay), triệt tiêu việc nhìn trộm lợi nhuận chưa thành hiện thực.
        horizon_cutoff = (date.today() - timedelta(days=int(horizon))).strftime("%Y-%m-%d")
        query = (
            db.query(StrategyFactorSnapshot, StrategyOutcome.outcome_return_pct)
            .join(
                StrategyOutcome,
                StrategyFactorSnapshot.signal_run_id == StrategyOutcome.signal_run_id,
            )
            .filter(
                StrategyOutcome.horizon_days == int(horizon),
                StrategyOutcome.outcome_status.in_(("evaluated", "hit_target", "hit_stop")),
                StrategyOutcome.outcome_return_pct.isnot(None),
                StrategyFactorSnapshot.snapshot_date >= cutoff,
                StrategyFactorSnapshot.snapshot_date <= horizon_cutoff,
            )
        )
        if market:
            query = query.filter(StrategyFactorSnapshot.stock_market == market)
        rows = query.all()

        all_xy: dict[str, tuple[list[float], list[float]]] = {f: ([], []) for f in FACTOR_FIELDS}
        by_date: dict[str, dict[str, tuple[list[float], list[float]]]] = {}

        for snap, ret in rows:
            try:
                r = float(ret)
            except Exception:
                continue
            for f in FACTOR_FIELDS:
                v = getattr(snap, f, None)
                if v is None:
                    continue
                try:
                    fv = float(v)
                except Exception:
                    continue
                all_xy[f][0].append(fv)
                all_xy[f][1].append(r)
                d = snap.snapshot_date or ""
                slot = by_date.setdefault(d, {}).setdefault(f, ([], []))
                slot[0].append(fv)
                slot[1].append(r)

        factors: dict[str, dict] = {}
        for f in FACTOR_FIELDS:
            xs, ys = all_xy[f]
            ic = spearman(xs, ys) if len(xs) >= min_samples else None
            period_ics: list[float] = []
            for _d, facmap in by_date.items():
                if f not in facmap:
                    continue
                pxs, pys = facmap[f]
                if len(pxs) >= min_period_samples:
                    di = spearman(pxs, pys)
                    if di is not None:
                        period_ics.append(di)
            ir = None
            if len(period_ics) >= 3:
                m = fmean(period_ics)
                s = stdev(period_ics)
                ir = (m / s) if s > 0 else None
            factors[f] = {
                "ic": round(ic, 4) if ic is not None else None,
                "ir": round(ir, 4) if ir is not None else None,
                "sample_size": len(xs),
                "ic_periods": len(period_ics),
            }

        return {"horizon": int(horizon), "days": int(days), "market": market, "factors": factors}
    except Exception as e:
        logger.warning(f"[因子评估] IC 计算失败: {e}")
        return {"horizon": int(horizon), "days": int(days), "market": market,
                "factors": {}, "error": str(e)}
    finally:
        if own:
            db.close()
