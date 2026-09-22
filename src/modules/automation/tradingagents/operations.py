"""Liên động tăng/giảm gấp trong phiên: tự kích hoạt phân tích chuyên sâu TradingAgents.

Thiết kế:
- intraday_monitor phân tích xong một mã thì gọi `try_auto_trigger`
- Điều kiện kích hoạt (MVP): |change_pct| >= threshold (mặc định 5%, đọc từ cấu hình tradingagents)
- Rào chắn: thời gian chờ (mặc định 24h) + ngân sách tháng (dùng lại cost_tracker)
- Mặc định tắt (enabled=false), phải bật tường minh trong «Cấu hình chuyên sâu» ở danh sách Agents

Nửa dưới của cùng tệp này lo phần điền ngược khuyến nghị lịch sử và đối chiếu quyết định
lịch sử; những phần đó không tham gia lượt chạy đồ thị chính của TradingAgents.

Vì sao không dùng thẳng BaseAgent.run:
- intraday_monitor chạy rất nhiều mã trong một vòng, mã nào cũng có thể kích hoạt, nên cần fire-and-forget
- Phân tích TA sau khi kích hoạt đi qua hàng đợi bất đồng bộ của chính trigger_agent_for_stock, tránh chặn vòng chính
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from src.platform.marketdata.collectors.kline_collector import KlineCollector
from src.platform.marketdata.models import MarketCode
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import (
    AgentConfig,
    AnalysisHistory,
    StockSuggestion,
)

logger = logging.getLogger(__name__)

# Các hàm này là cửa vận hành ngoại vi cho API / giám sát trong phiên, không tham gia luồng chạy chính của đồ thị.
__all__ = [
    "backfill_tradingagents_suggestions",
    "build_history_comparison",
    "fire_and_forget_trigger",
    "should_auto_trigger",
    "try_auto_trigger",
]

DEFAULT_CHANGE_PCT_THRESHOLD = 5.0
DEFAULT_COOLDOWN_HOURS = 24


def _read_auto_trigger_config(db: Session) -> dict | None:
    """Đọc cấu hình auto_trigger từ AgentConfig.raw_config.

    Returns:
        {
            "enabled": bool,
            "change_pct_threshold": float,
            "cooldown_hours": int,
        } hoặc None (chưa cấu hình/chưa bật)
    """
    agent = db.query(AgentConfig).filter(AgentConfig.name == "tradingagents").first()
    if not agent:
        return None
    raw = agent.raw_config or {}
    auto = raw.get("auto_trigger") or {}
    if not auto.get("enabled"):
        return None
    return {
        "enabled": True,
        "change_pct_threshold": float(auto.get("change_pct_threshold") or DEFAULT_CHANGE_PCT_THRESHOLD),
        "cooldown_hours": int(auto.get("cooldown_hours") or DEFAULT_COOLDOWN_HOURS),
    }


def _within_cooldown(db: Session, stock_symbol: str, cooldown_hours: int) -> bool:
    """Kiểm tra xem trong N giờ gần nhất đã kích hoạt phân tích TA cho mã này chưa (từ bất kỳ nguồn nào)."""
    cutoff = datetime.utcnow() - timedelta(hours=cooldown_hours)
    recent = (
        db.query(AnalysisHistory)
        .filter(
            AnalysisHistory.agent_name == "tradingagents",
            AnalysisHistory.stock_symbol == stock_symbol,
            AnalysisHistory.created_at >= cutoff,
        )
        .first()
    )
    return recent is not None


def _budget_allows(db: Session) -> bool:
    """Kiểm tra ngân sách tháng còn dư không. Ngân sách đọc từ raw_config.monthly_budget_usd của tradingagents."""
    try:
        from src.modules.automation.tradingagents.observability import check_budget
    except ImportError:
        return True

    agent = db.query(AgentConfig).filter(AgentConfig.name == "tradingagents").first()
    if not agent:
        return True
    raw = agent.raw_config or {}
    budget = float(raw.get("monthly_budget_usd") or 0.0)
    if budget <= 0:
        return True  # Không đặt trần = không giới hạn

    try:
        status = check_budget(budget)
        return not status.get("exceeded", False)
    except Exception as e:
        logger.warning(f"[auto_trigger] 预算检查失败,放行: {e}")
        return True


def should_auto_trigger(
    stock_symbol: str,
    change_pct: float | None,
) -> tuple[bool, str]:
    """Xét xem có nên kích hoạt phân tích chuyên sâu TA không.

    Returns:
        (should_trigger, reason)
    """
    if change_pct is None:
        return False, "无涨跌幅数据"

    db = SessionLocal()
    try:
        cfg = _read_auto_trigger_config(db)
        if not cfg:
            return False, "auto_trigger 未启用"

        if abs(change_pct) < cfg["change_pct_threshold"]:
            return False, f"涨跌幅 {change_pct:+.2f}% 未达阈值 {cfg['change_pct_threshold']}%"

        if _within_cooldown(db, stock_symbol, cfg["cooldown_hours"]):
            return False, f"冷却中(最近 {cfg['cooldown_hours']}h 已触发过)"

        if not _budget_allows(db):
            return False, "月度预算已用完"

        return True, f"涨跌幅 {change_pct:+.2f}% 达阈值 {cfg['change_pct_threshold']}%"
    finally:
        db.close()


def fire_and_forget_trigger(stock: Any, source_agent: str = "intraday_monitor") -> str | None:
    """Kích hoạt phân tích chuyên sâu TA bất đồng bộ, không chặn bên gọi.

    Args:
        stock: đối tượng có ít nhất symbol/name/market (StockData hoặc ORM Stock)
        source_agent: tên agent nguồn kích hoạt (dùng cho nhật ký/trace_id)

    Returns:
        trace_id hoặc None (kích hoạt thất bại)
    """
    import time as _time

    try:
        from server import trigger_agent_for_stock
    except ImportError:
        logger.warning("[auto_trigger] server.trigger_agent_for_stock 不可用,跳过")
        return None

    symbol = getattr(stock, "symbol", None)
    if not symbol:
        return None

    trace_id = f"auto-{source_agent}-{symbol}-{int(_time.time() * 1000)}"

    async def _run():
        try:
            await trigger_agent_for_stock(
                "tradingagents",
                stock,
                stock_agent_id=None,
                bypass_throttle=True,
                bypass_market_hours=True,
                suppress_notify=False,
                trace_id=trace_id,
                force_refresh=False,
            )
            logger.info(f"[auto_trigger] TA 联动触发完成 - {symbol} (trace={trace_id})")
        except Exception:
            logger.exception(f"[auto_trigger] TA 联动触发失败 - {symbol}")

    try:
        # Ưu tiên xếp lịch trên event loop hiện tại; không có loop thì dự phòng bằng cách mở luồng mới
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(_run())
        else:
            import threading
            t = threading.Thread(target=lambda: asyncio.run(_run()), daemon=True)
            t.start()
    except RuntimeError:
        import threading
        t = threading.Thread(target=lambda: asyncio.run(_run()), daemon=True)
        t.start()

    return trace_id


def try_auto_trigger(stock: Any, source_agent: str = "intraday_monitor") -> str | None:
    """Gọi gộp: xét + kích hoạt.

    Cho intraday_monitor.analyze gọi sau khi xong. Trả về trace_id hoặc None.
    """
    symbol = getattr(stock, "symbol", "") or ""
    change_pct = getattr(stock, "change_pct", None)

    ok, reason = should_auto_trigger(symbol, change_pct)
    if not ok:
        logger.debug(f"[auto_trigger] 不触发 {symbol}: {reason}")
        return None

    logger.info(f"[auto_trigger] 触发 TA 深度分析 - {symbol} ({reason})")
    return fire_and_forget_trigger(stock, source_agent)


# ============================================================================
# Suggestion backfill
# ============================================================================

def backfill_tradingagents_suggestions(days: int = 7) -> dict:
    """Điền ngược các bản ghi tradingagents trong analysis_history N ngày gần nhất vào stock_suggestions.

    Returns:
        {"checked": int, "written": int, "skipped": int}
    """
    from src.modules.automation.suggestion_pool import save_suggestion

    cutoff_date = (date.today() - timedelta(days=days)).isoformat()

    db = SessionLocal()
    checked = written = skipped = 0
    try:
        records = (
            db.query(AnalysisHistory)
            .filter(
                AnalysisHistory.agent_name == "tradingagents",
                AnalysisHistory.analysis_date >= cutoff_date,
            )
            .all()
        )

        for r in records:
            checked += 1
            raw = r.raw_data or {}
            sug = raw.get("suggestion") or {}
            action = (sug.get("action") or "hold").lower()
            action_label = sug.get("action_label") or "持有"
            confidence = sug.get("confidence")

            # Kiểm tra stock_suggestions đã có chưa (cùng mã + cùng agent + cùng action + trong 24h qua)
            # Làm gọn: cứ gọi save, chính save_suggestion sẽ lo khử trùng lặp
            existing = (
                db.query(StockSuggestion)
                .filter(
                    StockSuggestion.stock_symbol == r.stock_symbol,
                    StockSuggestion.agent_name == "tradingagents",
                    StockSuggestion.action == action,
                )
                .first()
            )
            if existing:
                skipped += 1
                continue

            confidence_text = (
                f" (置信度 {confidence:.1f}/10)"
                if isinstance(confidence, (int, float))
                else ""
            )

            # Suy ra thị trường (bản ghi phân tích không lưu, suy đơn giản từ stock_symbol)
            symbol = r.stock_symbol
            if symbol.isdigit() and len(symbol) == 6:
                market = "CN"
            elif symbol.isalpha():
                market = "US"
            elif symbol.isdigit() and len(symbol) == 5:
                market = "HK"
            else:
                market = "CN"

            # Lấy tên cổ phiếu từ bản ghi AnalysisHistory (nếu có)
            stock_name = ""
            try:
                from src.platform.persistence.models import Stock
                stk = db.query(Stock).filter(Stock.symbol == symbol).first()
                if stk:
                    stock_name = stk.name or ""
                    market = stk.market or market
            except Exception:
                pass

            ok = save_suggestion(
                stock_symbol=symbol,
                stock_name=stock_name or symbol,
                stock_market=market,
                action=action,
                action_label=f"{action_label}{confidence_text}",
                agent_name="tradingagents",
                agent_label="TradingAgents 深度",
                signal=(sug.get("signal") or "")[:500],
                reason=(sug.get("reason") or "")[:1000],
                expires_hours=24,
                ai_response=(r.content or "")[:2000],
                meta={
                    "cost_usd": raw.get("cost_usd", 0),
                    "decision": raw.get("decision", "HOLD"),
                    "confidence": confidence,
                    "backfilled_at": str(date.today()),
                },
            )
            if ok:
                written += 1
            else:
                skipped += 1

        logger.info(
            f"[TA backfill] 检查 {checked} 条历史记录,写入 {written} 条建议,跳过 {skipped} 条"
        )
        return {"checked": checked, "written": written, "skipped": skipped}
    except Exception as e:
        logger.warning(f"[TA backfill] 失败,跳过: {e}")
        return {"checked": checked, "written": written, "skipped": skipped, "error": str(e)}
    finally:
        db.close()



# ============================================================================
# History comparison
# ============================================================================

def _resolve_market(market: str) -> MarketCode:
    code = (market or "CN").strip().upper()
    if code == "US":
        return MarketCode.US
    if code == "HK":
        return MarketCode.HK
    return MarketCode.CN


def _classify_hit(action: str, ret_pct: float | None) -> bool | None:
    """Xét theo action và tỷ suất lợi nhuận sau đó xem quyết định có "trúng" không."""
    if ret_pct is None:
        return None
    if action == "buy":
        return ret_pct > 0
    if action == "sell":
        return ret_pct < 0
    if action == "hold":
        return abs(ret_pct) < 2.0
    return None


def _find_close_on_or_after(klines_by_date: dict[str, float], target: str) -> tuple[str, float] | None:
    """Từ ngày target tìm ngược lại giá đóng cửa của phiên giao dịch gần nhất. Tra lùi tối đa 7 ngày (nghỉ lễ)."""
    base = date.fromisoformat(target)
    for offset in range(8):
        d = (base + timedelta(days=offset)).isoformat()
        if d in klines_by_date:
            return d, klines_by_date[d]
    return None


def _find_close_after_n_trading_days(
    sorted_dates: list[str],
    base_date: str,
    n: int,
    klines_by_date: dict[str, float],
) -> float | None:
    """Giá đóng cửa sau N phiên giao dịch kể từ base_date. base_date bắt buộc đã là phiên giao dịch."""
    try:
        idx = sorted_dates.index(base_date)
    except ValueError:
        return None
    target_idx = idx + n
    if target_idx >= len(sorted_dates):
        return None
    return klines_by_date[sorted_dates[target_idx]]


def build_history_comparison(
    stock_symbol: str,
    market: str = "CN",
    days: int = 90,
) -> dict:
    """Dựng dữ liệu đối chiếu quyết định lịch sử của TradingAgents cho một mã.

    Args:
        stock_symbol: mã cổ phiếu
        market: CN / US / HK
        days: truy lịch sử TA bao nhiêu ngày

    Returns:
        {
            "items": [...],     # xếp theo analysis_date giảm dần
            "stats": {...},     # tỷ lệ trúng + lợi nhuận bình quân
        }
    """
    symbol = (stock_symbol or "").strip()
    if not symbol:
        return {"items": [], "stats": _empty_stats()}

    cutoff_date = (date.today() - timedelta(days=days)).isoformat()

    db = SessionLocal()
    try:
        records = (
            db.query(AnalysisHistory)
            .filter(
                AnalysisHistory.agent_name == "tradingagents",
                AnalysisHistory.stock_symbol == symbol,
                AnalysisHistory.analysis_date >= cutoff_date,
            )
            .order_by(AnalysisHistory.analysis_date.desc())
            .all()
        )
    except Exception as e:
        logger.warning(f"[TA history] 查询失败: {e}")
        db.close()
        return {"items": [], "stats": _empty_stats()}
    finally:
        db.close()

    if not records:
        return {"items": [], "stats": _empty_stats()}

    # Kéo nến lịch sử (số ngày nhìn lại + đệm 30 ngày để quyết định sớm nhất cũng tính được lợi nhuận 20 phiên)
    try:
        collector = KlineCollector(_resolve_market(market))
        klines = collector.get_klines(symbol, days=days + 40)
    except Exception as e:
        logger.warning(f"[TA history] 拉 K线失败: {e}")
        klines = []

    klines_by_date = {k.date: k.close for k in klines}
    sorted_dates = sorted(klines_by_date.keys())

    items: list[dict] = []
    for r in records:
        raw = r.raw_data or {}
        sug = raw.get("suggestion") or {}
        action = (sug.get("action") or "hold").lower()
        confidence = sug.get("confidence")
        cost_usd = raw.get("cost_usd")
        # Giá phân tích ưu tiên lấy "giá thời gian thực lúc phân tích" đã lưu (hiện ngay), giá đóng cửa của nến chỉ là dự phòng
        stored_price = raw.get("price_at_analysis")
        stored_price = round(float(stored_price), 2) if isinstance(stored_price, (int, float)) else None

        base = _find_close_on_or_after(klines_by_date, r.analysis_date)
        if base is None:
            items.append({
                "trace_id": "",
                "analysis_date": r.analysis_date,
                "action": action,
                "action_label": sug.get("action_label") or _action_to_label(action),
                "confidence": confidence,
                "cost_usd": cost_usd,
                "price_at_analysis": stored_price,
                "return_1d_pct": None,
                "return_5d_pct": None,
                "return_20d_pct": None,
                "hit_20d": None,
            })
            continue

        base_date, base_close = base
        ret = {}
        for n_days in (1, 5, 20):
            close_n = _find_close_after_n_trading_days(sorted_dates, base_date, n_days, klines_by_date)
            ret[n_days] = (
                round((close_n - base_close) / base_close * 100, 2) if close_n is not None else None
            )

        items.append({
            "trace_id": "",
            "analysis_date": r.analysis_date,
            "action": action,
            "action_label": sug.get("action_label") or _action_to_label(action),
            "confidence": confidence,
            "cost_usd": cost_usd,
            "price_at_analysis": stored_price if stored_price is not None else round(base_close, 2),
            "return_1d_pct": ret[1],
            "return_5d_pct": ret[5],
            "return_20d_pct": ret[20],
            "hit_20d": _classify_hit(action, ret[20]),
        })

    return {"items": items, "stats": _compute_stats(items)}


def _action_to_label(action: str) -> str:
    return {"buy": "买入", "sell": "卖出", "hold": "持有"}.get(action, action)


def _empty_stats() -> dict:
    return {
        "total": 0,
        "buy_count": 0,
        "sell_count": 0,
        "hold_count": 0,
        "buy_hit_rate": None,
        "sell_hit_rate": None,
        "hold_hit_rate": None,
        "overall_hit_rate": None,
        "avg_return_20d_pct": None,
    }


def _compute_stats(items: list[dict]) -> dict:
    """Thống kê: chỉ dựa trên các mục đã có lợi nhuận 20 ngày."""
    scored = [x for x in items if x.get("return_20d_pct") is not None]
    if not scored:
        return {**_empty_stats(), "total": len(items)}

    def _rate(action: str) -> float | None:
        subset = [x for x in scored if x["action"] == action]
        if not subset:
            return None
        hits = sum(1 for x in subset if x.get("hit_20d"))
        return round(hits / len(subset), 3)

    overall_hits = sum(1 for x in scored if x.get("hit_20d"))
    avg_return = round(sum(x["return_20d_pct"] for x in scored) / len(scored), 2)

    return {
        "total": len(items),
        "buy_count": sum(1 for x in items if x["action"] == "buy"),
        "sell_count": sum(1 for x in items if x["action"] == "sell"),
        "hold_count": sum(1 for x in items if x["action"] == "hold"),
        "buy_hit_rate": _rate("buy"),
        "sell_hit_rate": _rate("sell"),
        "hold_hit_rate": _rate("hold"),
        "overall_hit_rate": round(overall_hits / len(scored), 3),
        "avg_return_20d_pct": avg_return,
    }
