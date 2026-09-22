"""Giao diện adapter cho khung định lượng (dành sẵn ở Phase 4, nhẹ).

Định nghĩa giao ước thống nhất cho backend kiểm thử lịch sử, để sau này cắm cài đặt khác
mà không phải sửa tầng trên:
- Dựng sẵn (mặc định, luôn có): src/core/backtest (lõi thuần Python nhẹ, Phase 0)
- Nâng cấp tùy chọn (theo lộ trình, mặc định không cài, giữ bản tự host nhẹ):
    · vectorbt —— kiểm thử lịch sử hàng loạt kiểu vector hóa / dò tham số nhân tố theo lưới
    · rqalpha  —— khớp lệnh cổ phiếu A sát thực tế về chi phí (thuế trước bạ/biên độ trần sàn/lịch giao dịch)
    · qlib     —— nghiên cứu nhân tố ML (Alpha158/360 + LightGBM…)

Ở đây chỉ khai báo giao diện + dò «đã cài backend nào», lúc nối thật thì mỗi cái viết một
adapter hiện thực giao ước này.
Căn cứ chọn lựa xem .docs/quant-framework-comparison.md.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BacktestAdapter(Protocol):
    """Giao diện thống nhất của backend kiểm thử lịch sử. backtest.engine.Backtester dựng sẵn đã đáp ứng run()."""

    name: str

    def run(self, signals: list, bars_by_symbol: dict):  # noqa: D401
        """Kiểm thử lịch sử một lô tín hiệu, trả về đối tượng kết quả kèm metrics."""
        ...


_OPTIONAL_BACKENDS = (
    ("vectorbt", "vectorbt"),
    ("rqalpha", "rqalpha"),
    ("qlib", "qlib"),
)


def available_backends() -> dict[str, bool]:
    """Dò các backend kiểm thử lịch sử dùng được. Bản dựng sẵn luôn có; các phụ thuộc nặng tùy chọn thì trả về theo tình hình đã cài.

    Cho giao diện / tài liệu hiện môi trường hiện tại đã cài backend nào, không kích hoạt việc cài đặt nào cả.
    """
    backends: dict[str, bool] = {"builtin": True}
    for module_name, key in _OPTIONAL_BACKENDS:
        try:
            __import__(module_name)
            backends[key] = True
        except Exception:
            backends[key] = False
    return backends
