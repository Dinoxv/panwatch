"""Mô hình chi phí giao dịch cổ phiếu A — dùng chung cho kiểm thử lịch sử (Phase 0) và mô phỏng bàn giao dịch (Phase 1).

Khẩu độ chi phí (sau khi thuế trước bạ hạ ngày 2023-08-28):
- Thuế trước bạ: **chỉ chiều bán** 0,05%
- Phí môi giới: cả hai chiều, mặc định 0,025%, mỗi lệnh tối thiểu 5 đồng
- Phí chuyển nhượng: cả hai chiều, 0,001% giá trị khớp lệnh (Thượng Hải và Thâm Quyến thống nhất, từ 2022-04)
- Trượt giá: điểm cơ bản cấu hình được (mặc định 5bps), giá mua trượt lên / giá bán trượt xuống, mô phỏng chi phí tác động

Trượt giá thể hiện ở giá khớp thực tế (fill_price), không tính trùng vào phí tường minh;
phí tường minh = môi giới + thuế trước bạ + phí chuyển nhượng.
Biến động tiền mặt (cash_delta) = mua thì âm, bán thì dương, đã trừ toàn bộ chi phí và
trượt giá; PnL là tổng cash_delta của hai chân mua và bán.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostConfig:
    """Tham số chi phí (cấu hình được; giá trị mặc định bám sát thực tế nhà đầu tư cá nhân ở cổ phiếu A)."""

    commission_rate: float = 0.00025   # Tỷ lệ hoa hồng (cả hai chiều) 0,025%
    min_commission: float = 5.0        # Hoa hồng tối thiểu mỗi lệnh (đồng)
    stamp_duty_rate: float = 0.0005    # Thuế tem (chỉ chiều bán) 0,05%
    transfer_fee_rate: float = 0.00001  # Phí chuyển nhượng (cả hai chiều) 0,001%
    slippage_bps: float = 5.0          # Trượt giá (điểm cơ bản, cả hai chiều; 5bps = 0,05%)


@dataclass(frozen=True)
class Fill:
    """Kết quả ròng của một lần khớp lệnh (kèm bóc tách chi phí, tiện hiển thị và kiểm toán)."""

    side: str            # "buy" | "sell"
    price: float         # Giá danh nghĩa (giá tín hiệu / giá thị trường, chưa tính trượt giá)
    fill_price: float    # Giá khớp thực tế (đã tính trượt giá)
    quantity: int
    gross: float         # Giá trị khớp thực tế = fill_price * quantity
    commission: float
    stamp_duty: float
    transfer_fee: float
    slippage_cost: float  # Hao hụt do trượt giá = |fill_price - price| * quantity (chỉ để hiển thị)
    explicit_fees: float  # Phí tường minh = commission + stamp_duty + transfer_fee
    friction: float       # Tổng ma sát = explicit_fees + slippage_cost (chỉ để hiển thị)
    cash_delta: float     # Biến động tiền mặt: mua là âm, bán là dương (đã trừ phí tường minh; trượt giá đã nằm trong fill_price)


class CostModel:
    """Bộ tính chi phí giao dịch cổ phiếu A. Không liên quan tới luồng, dùng lại toàn cục được."""

    def __init__(self, config: CostConfig | None = None) -> None:
        self.cfg = config or CostConfig()

    def _apply_slippage(self, price: float, side: str) -> float:
        adj = price * self.cfg.slippage_bps / 10000.0
        return price + adj if side == "buy" else max(0.0, price - adj)

    def fill(self, side: str, price: float, quantity: int) -> Fill:
        """Tính chi phí và biến động tiền mặt của một lần khớp lệnh.

        Args:
            side: "buy" hoặc "sell"
            price: giá danh nghĩa (chưa gồm trượt giá)
            quantity: số cổ (số nguyên dương)
        """
        side = (side or "").strip().lower()
        if side not in ("buy", "sell"):
            raise ValueError(f"side 必须是 buy/sell,得到 {side!r}")
        qty = int(quantity)
        if qty <= 0 or price <= 0:
            raise ValueError(f"price/quantity 必须为正,得到 price={price} qty={quantity}")

        fill_price = self._apply_slippage(price, side)
        gross = fill_price * qty
        commission = max(gross * self.cfg.commission_rate, self.cfg.min_commission)
        stamp_duty = gross * self.cfg.stamp_duty_rate if side == "sell" else 0.0
        transfer_fee = gross * self.cfg.transfer_fee_rate
        slippage_cost = abs(fill_price - price) * qty
        explicit_fees = commission + stamp_duty + transfer_fee

        if side == "buy":
            cash_delta = -(gross + explicit_fees)
        else:
            cash_delta = gross - explicit_fees

        return Fill(
            side=side,
            price=float(price),
            fill_price=round(fill_price, 6),
            quantity=qty,
            gross=round(gross, 4),
            commission=round(commission, 4),
            stamp_duty=round(stamp_duty, 4),
            transfer_fee=round(transfer_fee, 4),
            slippage_cost=round(slippage_cost, 4),
            explicit_fees=round(explicit_fees, 4),
            friction=round(explicit_fees + slippage_cost, 4),
            cash_delta=round(cash_delta, 4),
        )

    def round_trip_pnl(
        self, entry_price: float, exit_price: float, quantity: int
    ) -> dict:
        """Lãi lỗ trọn vẹn của một vòng mua rồi bán (trừ toàn bộ chi phí). Tiện cho kiểm thử lịch sử từng lệnh và đối soát."""
        buy = self.fill("buy", entry_price, quantity)
        sell = self.fill("sell", exit_price, quantity)
        # Khẩu độ tiền mặt: tiền chi khi mua là -cash_delta (số dương), tiền thu khi bán là cash_delta
        invested = -buy.cash_delta
        proceeds = sell.cash_delta
        pnl = proceeds - invested
        pnl_pct = (pnl / invested * 100.0) if invested > 0 else 0.0
        total_cost = buy.friction + sell.friction
        return {
            "entry_price": float(entry_price),
            "exit_price": float(exit_price),
            "quantity": int(quantity),
            "invested": round(invested, 4),
            "proceeds": round(proceeds, 4),
            "pnl": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 4),
            "total_cost": round(total_cost, 4),
            "buy": buy,
            "sell": sell,
        }


# Thực thể mặc định toàn cục (có thể ghi đè bằng cấu hình)
DEFAULT_COST_MODEL = CostModel()
