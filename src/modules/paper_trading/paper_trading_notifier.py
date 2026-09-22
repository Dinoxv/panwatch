"""Thông báo sao chép lệnh của mô phỏng: đẩy thời gian thực khi mở/đóng vị thế, kế hoạch trước phiên, tóm tắt cuối ngày."""

from __future__ import annotations

import logging
from typing import Any

from src.platform.notifications.notifier import NotifierManager
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import (
    AppSettings,
    NotifyChannel,
    PaperTradingAccount,
    PaperTradingPosition,
    PaperTradingTrade,
    StrategySignalRun,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Đọc cấu hình
# ---------------------------------------------------------------------------

_CONFIG_KEYS = {
    "pt_notify_enabled": "false",
    "pt_notify_channel_ids": "",
    "pt_notify_realtime": "true",
    "pt_notify_premarket": "true",
    "pt_notify_summary": "true",
}


def _load_config() -> dict[str, str]:
    """Đọc cấu hình pt_notify_* từ bảng AppSettings."""
    db = SessionLocal()
    try:
        rows = (
            db.query(AppSettings)
            .filter(AppSettings.key.in_(_CONFIG_KEYS.keys()))
            .all()
        )
        cfg = dict(_CONFIG_KEYS)  # defaults
        for r in rows:
            cfg[r.key] = r.value or _CONFIG_KEYS.get(r.key, "")
        return cfg
    finally:
        db.close()


def _is_enabled() -> bool:
    cfg = _load_config()
    return cfg.get("pt_notify_enabled", "").lower() == "true"


def _is_mode_enabled(mode_key: str) -> bool:
    cfg = _load_config()
    if cfg.get("pt_notify_enabled", "").lower() != "true":
        return False
    return cfg.get(mode_key, "").lower() == "true"


# ---------------------------------------------------------------------------
# Dựng kênh
# ---------------------------------------------------------------------------

def _build_notifier() -> NotifierManager | None:
    """Dựng NotifierManager theo cấu hình, không có kênh nào dùng được thì trả None."""
    cfg = _load_config()
    if cfg.get("pt_notify_enabled", "").lower() != "true":
        return None

    db = SessionLocal()
    try:
        channel_ids_str = cfg.get("pt_notify_channel_ids", "").strip()
        if channel_ids_str:
            ids = [int(x.strip()) for x in channel_ids_str.split(",") if x.strip().isdigit()]
            channels = (
                db.query(NotifyChannel)
                .filter(NotifyChannel.id.in_(ids), NotifyChannel.enabled.is_(True))
                .all()
            )
        else:
            # Không chỉ định kênh thì dùng kênh mặc định
            channels = (
                db.query(NotifyChannel)
                .filter(NotifyChannel.enabled.is_(True), NotifyChannel.is_default.is_(True))
                .all()
            )

        if not channels:
            logger.debug("[模拟盘通知] 无可用通知渠道")
            return None

        mgr = NotifierManager()
        for ch in channels:
            mgr.add_channel(ch.type, ch.config or {})
        return mgr
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Định dạng thông điệp
# ---------------------------------------------------------------------------

EXIT_REASON_LABELS = {
    "stop_loss": "止损",
    "target_price": "止盈",
    "signal_reversal": "信号反转",
    "manual": "手动平仓",
}

STRATEGY_NAME_MAP = {
    "trend_follow": "趋势延续",
    "macd_golden": "MACD金叉",
    "volume_breakout": "放量突破",
    "pullback": "回踩确认",
    "rebound": "超跌反弹",
    "watchlist_agent": "Agent建议",
    "market_scan": "市场扫描",
    "momentum": "动量策略",
}


def _strategy_label(code: str) -> str:
    """Đổi mã chiến lược sang tên hiển thị."""
    return STRATEGY_NAME_MAP.get(code, code)


def _stock_display(symbol: str, market: str, name: str = "") -> str:
    """Dựng chữ hiển thị của mã kèm liên kết, bấm vào mã thì nhảy sang trang bảng giá."""
    from src.modules.administration.stock_link import stock_link_markdown
    label = f"{name} " if name else ""
    return f"{label}({stock_link_markdown(symbol, market)})"


def _format_entry_message(pos: dict, sig: dict | None) -> tuple[str, str]:
    """Định dạng thông báo mở vị thế, trả về (title, body). pos/sig là dict đã tuần tự hóa."""
    name = pos.get("stock_name") or pos["stock_symbol"]
    title = f"【模拟盘建仓】{name}"

    # Tỷ lệ lãi trên lỗ
    rr_str = ""
    entry_price = pos.get("entry_price", 0)
    stop_loss = pos.get("stop_loss", 0)
    target_price = pos.get("target_price", 0)
    if stop_loss and target_price and entry_price:
        risk = abs(entry_price - stop_loss)
        reward = abs(target_price - entry_price)
        if risk > 0:
            rr_str = f"\n盈亏比: {reward / risk:.1f}:1"

    score_str = ""
    strategy_code = pos.get("strategy_code", "")
    if sig:
        if sig.get("rank_score"):
            score_str = f" | 评分: {sig['rank_score']:.1f}"
        if sig.get("strategy_code"):
            strategy_code = sig["strategy_code"]

    stock_info = _stock_display(pos["stock_symbol"], pos["stock_market"], name)
    body = (
        f"股票: {stock_info}\n"
        f"方向: 买入\n"
        f"买入价: {entry_price:.2f} | 数量: {pos['quantity']} 股\n"
        f"止损价: {stop_loss:.2f} | 目标价: {target_price:.2f}\n"
        f"策略: {_strategy_label(strategy_code)}{score_str}"
        f"{rr_str}"
    )
    return title, body


def _format_exit_message(pos: dict, trade: dict) -> tuple[str, str]:
    """Định dạng thông báo đóng vị thế, trả về (title, body). pos/trade là dict đã tuần tự hóa."""
    name = pos.get("stock_name") or pos["stock_symbol"]
    pnl = trade["pnl"]
    pnl_sign = "+" if pnl >= 0 else ""
    title = f"【模拟盘平仓】{name} {pnl_sign}{pnl:.2f}"

    stock_info = _stock_display(pos["stock_symbol"], pos["stock_market"], name)
    reason = EXIT_REASON_LABELS.get(trade["exit_reason"], trade["exit_reason"])
    body = (
        f"股票: {stock_info}\n"
        f"平仓原因: {reason}\n"
        f"买入价: {trade['entry_price']:.2f} → 卖出价: {trade['exit_price']:.2f}\n"
        f"盈亏: {pnl_sign}{pnl:.2f} ({pnl_sign}{trade['pnl_pct']:.2f}%) | 持仓: {trade['holding_days']}天"
    )
    return title, body


def _dedup_signals(signals: list[StrategySignalRun]) -> list[tuple[StrategySignalRun, int]]:
    """Gộp trùng theo (stock_symbol, stock_market), giữ tín hiệu có rank_score cao nhất.
    Trả về [(signal, strategy_count), ...], đã xếp theo rank_score giảm dần.
    """
    seen: dict[tuple[str, str], tuple[StrategySignalRun, int]] = {}
    for sig in signals:
        key = (sig.stock_symbol, sig.stock_market)
        if key not in seen:
            seen[key] = (sig, 1)
        else:
            _, count = seen[key]
            seen[key] = (seen[key][0], count + 1)
    # Đã truy vấn theo rank_score giảm dần, chỉ cần giữ thứ tự xuất hiện lần đầu
    return list(seen.values())


def _format_premarket_plan(signals: list[StrategySignalRun], account: PaperTradingAccount) -> tuple[str, str]:
    """Định dạng kế hoạch trước phiên, trả về (title, body). Tín hiệu sẽ tự gộp trùng."""
    title = "【模拟盘盘前计划】"
    if not signals:
        return title, "今日无候选股票"

    deduped = _dedup_signals(signals)

    lines = [f"可用资金: {account.current_capital:,.2f}\n"]
    lines.append("今日候选:")
    for i, (sig, strat_count) in enumerate(deduped, 1):
        name = sig.stock_name or sig.stock_symbol
        from src.modules.administration.stock_link import stock_link_markdown
        link = stock_link_markdown(sig.stock_symbol, sig.stock_market)
        entry_range = ""
        if sig.entry_low and sig.entry_high:
            entry_range = f" 入场区间: {sig.entry_low:.2f}-{sig.entry_high:.2f}"
        score_str = f" 评分:{sig.rank_score:.1f}" if sig.rank_score else ""
        strat_label = _strategy_label(sig.strategy_code)
        if strat_count > 1:
            strat_label = f"{strat_label} 等{strat_count}个策略"
        lines.append(f"{i}. {name} ({link}){entry_range}{score_str} [{strat_label}]")

    return title, "\n".join(lines)


def _format_daily_summary(
    trades: list[PaperTradingTrade],
    positions: list[PaperTradingPosition],
    account: PaperTradingAccount,
) -> tuple[str, str]:
    """Định dạng tóm tắt cuối ngày, trả về (title, body)."""
    # Tổng tài sản
    positions_value = sum((p.current_price or p.entry_price) * p.quantity for p in positions)
    total_equity = account.current_capital + positions_value
    unrealized = sum(p.unrealized_pnl or 0 for p in positions)

    title = "【模拟盘日终摘要】"
    lines = [f"总资产: {total_equity:,.2f}"]

    # Đóng vị thế trong ngày
    if trades:
        day_pnl = sum(t.pnl for t in trades)
        pnl_sign = "+" if day_pnl >= 0 else ""
        lines.append(f"\n当日平仓 {len(trades)} 笔, 盈亏: {pnl_sign}{day_pnl:,.2f}")
        for t in trades:
            s = "+" if t.pnl >= 0 else ""
            reason = EXIT_REASON_LABELS.get(t.exit_reason, t.exit_reason)
            lines.append(f"  · {t.stock_name or t.stock_symbol}: {s}{t.pnl:,.2f} ({s}{t.pnl_pct:.2f}%) [{reason}]")
    else:
        lines.append("\n当日无平仓操作")

    # Lãi chưa thực hiện của vị thế
    if positions:
        u_sign = "+" if unrealized >= 0 else ""
        lines.append(f"\n持仓中 {len(positions)} 只, 浮动盈亏: {u_sign}{unrealized:,.2f}")
        for p in positions:
            pnl = p.unrealized_pnl or 0
            s = "+" if pnl >= 0 else ""
            lines.append(f"  · {p.stock_name or p.stock_symbol}: {s}{pnl:,.2f}")
    else:
        lines.append("\n当前无持仓")

    lines.append(f"\n可用资金: {account.current_capital:,.2f}")
    return title, "\n".join(lines)


# ---------------------------------------------------------------------------
# Hàm kích hoạt
# ---------------------------------------------------------------------------

async def notify_entry(pos: dict, sig: dict | None) -> None:
    """Thông báo mở vị thế (bất đồng bộ, hỏng thì chỉ ghi nhật ký). pos/sig là dict đã tuần tự hóa."""
    try:
        if not _is_mode_enabled("pt_notify_realtime"):
            return
        mgr = _build_notifier()
        if not mgr:
            return
        title, body = _format_entry_message(pos, sig)
        await mgr.notify(title, body)
    except Exception:
        logger.exception("[模拟盘通知] 建仓通知发送失败")


async def notify_exit(pos: dict, trade: dict) -> None:
    """Thông báo đóng vị thế (bất đồng bộ, hỏng thì chỉ ghi nhật ký). pos/trade là dict đã tuần tự hóa."""
    try:
        if not _is_mode_enabled("pt_notify_realtime"):
            return
        mgr = _build_notifier()
        if not mgr:
            return
        title, body = _format_exit_message(pos, trade)
        await mgr.notify(title, body)
    except Exception:
        logger.exception("[模拟盘通知] 平仓通知发送失败")


async def send_premarket_plan() -> None:
    """Thông báo kế hoạch trước phiên."""
    try:
        if not _is_mode_enabled("pt_notify_premarket"):
            return
        mgr = _build_notifier()
        if not mgr:
            return

        db = SessionLocal()
        try:
            account = db.query(PaperTradingAccount).first()
            if not account or not account.enabled:
                return

            # Loại các thị trường không giải ngân (tỷ trọng bằng 0)
            from src.modules.paper_trading.paper_trading_engine import ALL_MARKETS, market_allocations_or_default
            alloc = market_allocations_or_default(account)
            excluded = [m for m in ALL_MARKETS if alloc.get(m, 0.0) <= 0]
            query = (
                db.query(StrategySignalRun)
                .filter(
                    StrategySignalRun.status == "active",
                    StrategySignalRun.action.in_(["buy", "add"]),
                    StrategySignalRun.entry_low.isnot(None),
                    StrategySignalRun.entry_high.isnot(None),
                )
            )
            if excluded:
                query = query.filter(StrategySignalRun.stock_market.notin_(excluded))
            signals = query.order_by(StrategySignalRun.rank_score.desc()).all()

            title, body = _format_premarket_plan(signals, account)
            await mgr.notify(title, body)
        finally:
            db.close()
    except Exception:
        logger.exception("[模拟盘通知] 盘前计划发送失败")


async def send_daily_summary() -> None:
    """Thông báo tóm tắt cuối ngày."""
    try:
        if not _is_mode_enabled("pt_notify_summary"):
            return
        mgr = _build_notifier()
        if not mgr:
            return

        db = SessionLocal()
        try:
            account = db.query(PaperTradingAccount).first()
            if not account or not account.enabled:
                return

            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone.utc)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

            # Đã đóng vị thế trong ngày
            trades = (
                db.query(PaperTradingTrade)
                .filter(PaperTradingTrade.closed_at >= today_start)
                .order_by(PaperTradingTrade.closed_at.desc())
                .all()
            )

            # Đang nắm giữ
            positions = (
                db.query(PaperTradingPosition)
                .filter(PaperTradingPosition.status == "open")
                .all()
            )

            title, body = _format_daily_summary(trades, positions, account)
            await mgr.notify(title, body)
        finally:
            db.close()
    except Exception:
        logger.exception("[模拟盘通知] 日终摘要发送失败")


async def send_test_notification() -> dict:
    """Gửi thông báo thử, trả về kết quả."""
    mgr = _build_notifier()
    if not mgr:
        return {"success": False, "error": "通知未启用或无可用渠道"}
    result = await mgr.notify_with_result(
        "【模拟盘测试】",
        "这是一条测试通知，确认通知渠道配置正常。",
    )
    return result
