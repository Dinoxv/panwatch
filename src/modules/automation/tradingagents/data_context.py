"""Khớp ngữ cảnh dữ liệu cho TradingAgents.

Tệp này đồng thời lo phần tóm tắt tài chính cổ phiếu A, PortfolioContext,
instrument_context và siêu dữ liệu của mã, đổi dữ liệu nghiệp vụ của PanWatch thành ngữ
cảnh có cấu trúc mà TradingAgents tiêu thụ được.

Thu thập dữ liệu tài chính cổ phiếu A — dùng akshare kéo báo cáo tài chính thật cho các
chuyên viên phân tích của TradingAgents dùng.

Trước đây PanWatch không thu thập báo cáo tài chính, các công cụ
fundamentals/balance/cashflow/income đều trả về văn bản giữ chỗ, LLM không phân tích cơ
bản thật sự được. Module này dùng stock_financial_abstract của akshare kéo dữ liệu thật
của 2 kỳ gần nhất (lợi nhuận sau thuế thuộc công ty mẹ / doanh thu / ROE / biên lợi nhuận
gộp / hệ số nợ / dòng tiền kinh doanh…) rồi nhét vào công cụ tương ứng.

Chỉ hỗ trợ cổ phiếu A (6 chữ số). Hỏng thì trả None, toolkit_adapter lùi về dữ liệu quote nhẹ.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

# Gom về một cửa xuất duy nhất cho luồng “dữ liệu nghiệp vụ → ngữ cảnh TradingAgents”, để phía gọi không phải bận tâm tới các hàm kết xuất bên trong.
__all__ = [
    "build_stock_metadata_context",
    "fetch_financial_abstract",
    "patch_instrument_context",
    "render_balance_sheet",
    "render_cashflow",
    "render_fundamentals_summary",
    "render_income_statement",
    "to_tradingagents_portfolio",
]


def fetch_financial_abstract(symbol: str) -> dict | None:
    """Kéo tóm tắt tài chính của một mã cổ phiếu A, trả về dict có cấu trúc.

    Returns:
        {
            "periods": ["20260331", "20251231", ...],   # N kỳ gần nhất
            "indicators": {
                "归母净利润": {"20260331": -6.56e8, "20251231": -8.78e9, ...},
                ...
            },
            "categories": {
                "盈利能力": {"毛利率": {...}, "净资产收益率(ROE)": {...}},
                "成长能力": {...},
                ...
            },
        }
        hoặc None (akshare hỏng / không phải cổ phiếu A / dữ liệu rỗng)
    """
    if not (symbol and len(symbol) == 6 and symbol.isdigit()):
        return None
    try:
        import akshare as ak
    except ImportError:
        logger.warning("[TA fin] akshare 未安装,无法拉财报")
        return None

    try:
        df = ak.stock_financial_abstract(symbol=symbol)
    except Exception as e:
        logger.warning(f"[TA fin] stock_financial_abstract({symbol}) 失败: {e}")
        return None
    if df is None or df.empty:
        return None

    # Cấu trúc cột: [选项, 指标, 20260331, 20251231, ...] — lấy N kỳ gần nhất
    period_cols = [c for c in df.columns if str(c).isdigit() and len(str(c)) == 8]
    if not period_cols:
        return None
    recent_periods = period_cols[:6]  # Tối đa 6 kỳ (1,5 năm)

    indicators: dict[str, dict[str, float | None]] = {}
    categories: dict[str, dict[str, dict[str, float | None]]] = {}

    for _, row in df.iterrows():
        cat = str(row.get("选项") or "").strip()
        name = str(row.get("指标") or "").strip()
        if not name:
            continue
        values: dict[str, float | None] = {}
        for p in recent_periods:
            v = row.get(p)
            try:
                values[p] = float(v) if v is not None and str(v) not in ("nan", "NaN") else None
            except (TypeError, ValueError):
                values[p] = None
        indicators[name] = values
        if cat:
            categories.setdefault(cat, {})[name] = values

    return {
        "periods": recent_periods,
        "indicators": indicators,
        "categories": categories,
    }


def _fmt_num(v: float | None) -> str:
    if v is None:
        return "N/A"
    av = abs(v)
    if av >= 1e8:
        return f"{v / 1e8:.2f} 亿"
    if av >= 1e4:
        return f"{v / 1e4:.2f} 万"
    return f"{v:.2f}"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "N/A"
    return f"{v:.2f}%"


def _fmt_period(p: str) -> str:
    """20260331 → 2026Q1, 20251231 → 2025Q4 (báo cáo năm)"""
    if len(p) != 8:
        return p
    y, m, d = p[:4], p[4:6], p[6:8]
    q = {"03": "Q1", "06": "Q2", "09": "Q3", "12": "Q4"}.get(m, m)
    return f"{y}{q}"


def render_fundamentals_summary(data: dict) -> str:
    """Dựng phần tóm tắt tổng hợp mặt cơ bản (cho get_fundamentals dùng)."""
    periods = data.get("periods", [])[:4]
    ind = data.get("indicators", {})
    if not periods or not ind:
        return "[No financial data available]"

    lines = ["[Real Financial Data from PanWatch (akshare)]"]
    lines.append(f"Reporting periods: {' | '.join(_fmt_period(p) for p in periods)}")
    lines.append("")

    key_metrics = [
        ("营业总收入", _fmt_num),
        ("归母净利润", _fmt_num),
        ("扣非净利润", _fmt_num),
        ("基本每股收益", lambda v: f"{v:.2f} 元" if v is not None else "N/A"),
        ("毛利率", _fmt_pct),
        ("净资产收益率(ROE)", _fmt_pct),
        ("资产负债率", _fmt_pct),
        ("经营现金流量净额", _fmt_num),
    ]
    for name, fmt in key_metrics:
        vals = ind.get(name)
        if not vals:
            continue
        row = " | ".join(fmt(vals.get(p)) for p in periods)
        lines.append(f"- {name}: {row}")

    lines.append("")
    lines.append(
        "Note: This is REAL data pulled from official A-share financial reports. "
        "Use these numbers to ground your fundamental analysis (revenue trends, "
        "profitability, leverage). Do NOT invent additional numbers."
    )
    return "\n".join(lines)


def render_income_statement(data: dict) -> str:
    """Dựng báo cáo kết quả kinh doanh (cho get_income_statement dùng)."""
    periods = data.get("periods", [])[:4]
    ind = data.get("indicators", {})
    if not periods or not ind:
        return "[No income statement data]"
    lines = ["[Income Statement (real data from PanWatch / akshare)]"]
    lines.append(f"Periods: {' | '.join(_fmt_period(p) for p in periods)}")
    lines.append("")
    metrics = [
        ("营业总收入", _fmt_num),
        ("营业成本", _fmt_num),
        ("归母净利润", _fmt_num),
        ("净利润", _fmt_num),
        ("扣非净利润", _fmt_num),
        ("毛利率", _fmt_pct),
        ("销售净利率", _fmt_pct),
        ("期间费用率", _fmt_pct),
    ]
    for name, fmt in metrics:
        vals = ind.get(name)
        if not vals:
            continue
        row = " | ".join(fmt(vals.get(p)) for p in periods)
        lines.append(f"- {name}: {row}")
    return "\n".join(lines)


def render_balance_sheet(data: dict) -> str:
    """Dựng bảng cân đối kế toán (cho get_balance_sheet dùng)."""
    periods = data.get("periods", [])[:4]
    ind = data.get("indicators", {})
    if not periods or not ind:
        return "[No balance sheet data]"
    lines = ["[Balance Sheet (real data from PanWatch / akshare)]"]
    lines.append(f"Periods: {' | '.join(_fmt_period(p) for p in periods)}")
    lines.append("")
    metrics = [
        ("股东权益合计(净资产)", _fmt_num),
        ("每股净资产", lambda v: f"{v:.2f} 元" if v is not None else "N/A"),
        ("商誉", _fmt_num),
        ("资产负债率", _fmt_pct),
        ("总资产报酬率(ROA)", _fmt_pct),
        ("净资产收益率(ROE)", _fmt_pct),
    ]
    for name, fmt in metrics:
        vals = ind.get(name)
        if not vals:
            continue
        row = " | ".join(fmt(vals.get(p)) for p in periods)
        lines.append(f"- {name}: {row}")
    return "\n".join(lines)


def render_cashflow(data: dict) -> str:
    """Dựng báo cáo lưu chuyển tiền tệ (cho get_cashflow dùng)."""
    periods = data.get("periods", [])[:4]
    ind = data.get("indicators", {})
    if not periods or not ind:
        return "[No cash flow data]"
    lines = ["[Cash Flow Statement (real data from PanWatch / akshare)]"]
    lines.append(f"Periods: {' | '.join(_fmt_period(p) for p in periods)}")
    lines.append("")
    metrics = [
        ("经营现金流量净额", _fmt_num),
        ("每股现金流", lambda v: f"{v:.2f} 元" if v is not None else "N/A"),
    ]
    for name, fmt in metrics:
        vals = ind.get(name)
        if not vals:
            continue
        row = " | ".join(fmt(vals.get(p)) for p in periods)
        lines.append(f"- {name}: {row}")
    return "\n".join(lines)


# ============================================================================
# Portfolio and instrument context
# ============================================================================

def build_stock_metadata_context(
    stock_symbol: str,
    stock_name: str = "",
    market: str = "CN",
    current_price: float | None = None,
    industry: str = "",
) -> str:
    """Dựng siêu dữ liệu của mã, tránh để mô hình tra ngược từ ticker A/HK rồi đoán mò công ty."""
    if not stock_symbol:
        return ""

    market_label = {"CN": "中国 A 股", "HK": "港股", "US": "美股"}.get(market, market)
    lines = [
        "[Stock Metadata]",
        f"- Ticker: {stock_symbol}",
        f"- Company name: {stock_name or 'N/A'}",
        f"- Market: {market_label}",
    ]
    if industry:
        lines.append(f"- Industry: {industry}")
    if current_price and current_price > 0:
        lines.append(f"- Current price: {current_price:.2f}")
    lines.append(
        "- IMPORTANT: This is an A-share / HK / cross-market ticker. DO NOT guess the "
        "company from the ticker code; always use the company name above."
    )
    return "\n".join(lines)


def _finite_number(value: Any) -> float | None:
    """Đổi an toàn một giá trị có thể lấy từ cơ sở dữ liệu thành float hữu hạn."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def to_tradingagents_portfolio(portfolio: Any):
    """Đổi ``PortfolioInfo`` thành ``PortfolioContext`` của TradingAgents 0.5.0.

    Vị thế cùng ticker ở nhiều tài khoản được gộp theo giá vốn bình quân trọng số theo số
    lượng. Không có ảnh chụp tài khoản nào thì trả ``None``, để thượng nguồn phân biệt rõ
    "người dùng không đưa danh mục" với "danh mục tiền mặt/vị thế đều bằng không".
    """
    accounts: Iterable[Any] = getattr(portfolio, "accounts", ()) or ()
    accounts = list(accounts)
    if not accounts:
        return None

    from tradingagents.portfolio import PortfolioContext, Position

    cash = 0.0
    by_ticker: dict[str, list[tuple[float, float | None]]] = {}
    for account in accounts:
        available_funds = _finite_number(getattr(account, "available_funds", 0))
        if available_funds is not None:
            cash += available_funds

        for position in getattr(account, "positions", ()) or ():
            ticker = str(getattr(position, "symbol", "") or "").strip().upper()
            quantity = _finite_number(getattr(position, "quantity", None))
            # TradingAgents 0.5.0 dùng số dương cho vị thế mua, số âm cho vị thế bán; ở đây chỉ lọc bỏ
            # khối lượng bằng 0 và dữ liệu rác, không được coi vị thế bán là “không có vị thế” rồi bỏ đi.
            if not ticker or quantity is None or quantity == 0:
                continue
            average_price = _finite_number(getattr(position, "cost_price", None))
            by_ticker.setdefault(ticker, []).append((quantity, average_price))

    positions = []
    for ticker, lots in by_ticker.items():
        quantity = sum(lot_quantity for lot_quantity, _ in lots)
        priced_lots = [
            (lot_quantity, average_price)
            for lot_quantity, average_price in lots
            if average_price is not None
        ]
        # Dùng trị tuyệt đối của khối lượng làm trọng số cho giá vốn: cùng chiều thì kết quả y như logic cũ, còn khi trộn mua - bán
        # cũng không sinh ra giá bình quân cực đoan vô nghĩa vì khối lượng ròng gần 0; quantity vẫn giữ dấu của giá trị ròng.
        total_abs_quantity = sum(abs(lot_quantity) for lot_quantity, _ in priced_lots)
        average_price = (
            sum(abs(lot_quantity) * price for lot_quantity, price in priced_lots)
            / total_abs_quantity
            if len(priced_lots) == len(lots) and total_abs_quantity > 0
            else None
        )
        positions.append(
            Position(ticker=ticker, quantity=quantity, average_price=average_price)
        )

    return PortfolioContext(cash=cash, positions=positions)


def patch_instrument_context(graph: Any, metadata_context: str) -> None:
    """Tiêm siêu dữ liệu mã của PanWatch vào ``instrument_context`` của TradingAgents 0.5.0.

    ``past_context`` là điểm mở rộng mà thượng nguồn dùng cho ký ức nghiên cứu lịch sử,
    nhét siêu dữ liệu nghiệp vụ của mã vào đó sẽ làm lệch ngữ nghĩa của prompt, và khiến
    lần phát lại nghiên cứu sau coi thông tin cổ phiếu lần này là kinh nghiệm lịch sử.
    ``Propagator.create_initial_state`` của 0.5.0 đã phơi ``instrument_context``, nên chỉ
    bọc một lần ở cấp thực thể tại lối vào này, và chuyển trọn portfolio/future kwargs.
    """
    if not metadata_context:
        return

    propagator = getattr(graph, "propagator", None)
    if propagator is None or not hasattr(propagator, "create_initial_state"):
        logger.warning("[TA context] propagator.create_initial_state 不存在，跳过元数据注入")
        return

    original = propagator.create_initial_state

    def _patched(
        company_name: str,
        trade_date: str,
        asset_type: str = "stock",
        past_context: str = "",
        instrument_context: str = "",
        portfolio_context: str = "",
        **kwargs: Any,
    ):
        merged = metadata_context
        if instrument_context:
            merged = f"{merged}\n\n---\n\n{instrument_context}"
        return original(
            company_name,
            trade_date,
            asset_type=asset_type,
            past_context=past_context,
            instrument_context=merged,
            portfolio_context=portfolio_context,
            **kwargs,
        )

    propagator.create_initial_state = _patched  # type: ignore[method-assign]
    logger.info("[TA context] 已注入 %s 字符标的元数据到 instrument_context", len(metadata_context))


def patch_past_context(graph: Any, metadata_context: str) -> None:
    """Bí danh cho bên gọi cũ; mã mới nên dùng :func:`patch_instrument_context`."""
    patch_instrument_context(graph, metadata_context)
