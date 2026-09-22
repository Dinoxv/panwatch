"""Agent theo dõi trong phiên - giám sát vị thế thời gian thực, để AI xét có cần cảnh báo không"""

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, date, timezone
from pathlib import Path

from src.modules.automation.base import BaseAgent, AgentContext, AnalysisResult
from src.platform.marketdata.collectors.kline_collector import KlineCollector
from src.modules.research.analysis_history import get_latest_analysis, get_analysis
from src.modules.research.context_builder import ContextBuilder
from src.modules.research.context_store import (
    save_agent_context_run,
    save_agent_prediction_outcome,
)
from src.modules.automation.suggestion_pool import save_suggestion
from src.modules.research.signals import SignalPackBuilder
from src.modules.research.signals.structured_output import try_parse_action_json
from src.platform.marketdata.models import MarketCode, StockData, MARKETS

logger = logging.getLogger(__name__)


def is_market_trading(market: MarketCode) -> bool:
    """Xét theo thị trường xem có đang trong giờ giao dịch không."""
    market_def = MARKETS.get(market)
    if not market_def:
        return False
    return market_def.is_trading_time()


def market_label(market: MarketCode) -> str:
    if market == MarketCode.CN:
        return "A股"
    if market == MarketCode.HK:
        return "港股"
    if market == MarketCode.US:
        return "美股"
    return market.value


# Chuẩn hóa khuyến nghị hành động
SUGGESTION_TYPES = {
    "建仓": "buy",  # Mở vị thế mới
    "加仓": "add",  # Gia tăng vị thế đang có
    "减仓": "reduce",  # Giảm bớt vị thế
    "清仓": "sell",  # Bán toàn bộ
    "持有": "hold",  # Giữ nguyên hiện trạng
    "观望": "watch",  # Tạm thời chưa hành động
}

PROMPT_PATH = Path(__file__).parent.parent.parent.parent / "prompts" / "intraday_monitor.txt"


class IntradayMonitorAgent(BaseAgent):
    """
    Agent theo dõi trong phiên

    Đặc điểm:
    - Chế độ từng mã (single): phân tích lần lượt, mỗi mã gửi thông báo riêng
    - AI tự xét: đưa dữ liệu mã cho AI, để AI quyết định có đáng cảnh báo không
    - Tiết lưu thông báo: cùng một mã trong thời gian ngắn không báo lặp
    - Phân tích kỹ thuật: gồm nến và chỉ báo kỹ thuật
    """

    name = "intraday_monitor"
    display_name = "盘中监测"
    description = "交易时段实时监控持仓，AI 判断是否有值得关注的信号"

    def __init__(
        self,
        throttle_minutes: int = 30,
        bypass_throttle: bool = False,
        bypass_market_hours: bool = False,
        event_only: bool = True,
        price_alert_threshold: float = 3.0,
        volume_alert_ratio: float = 2.0,
        stop_loss_warning: float = -5.0,
        take_profit_warning: float = 10.0,
    ):
        """
        Args:
            throttle_minutes: khoảng cách thông báo cho cùng một mã (phút)
            bypass_throttle: có bỏ qua tiết lưu không (dùng khi test)
            bypass_market_hours: có bỏ qua cổng giờ giao dịch không (chỉ cho tình huống phân tích tay)
            price_alert_threshold: biên độ vượt ngưỡng thì coi là biến động giá (%)
            volume_alert_ratio: tỷ lệ khối lượng vượt ngưỡng thì coi là bùng khối lượng bất thường
            stop_loss_warning: lỗ tạm tính vượt ngưỡng thì cảnh báo cắt lỗ (%)
            take_profit_warning: lãi tạm tính vượt ngưỡng thì nhắc chốt lời (%)
        """
        self.throttle_minutes = throttle_minutes
        self.bypass_throttle = bypass_throttle
        self.bypass_market_hours = bypass_market_hours
        self.event_only = event_only
        self.price_alert_threshold = price_alert_threshold
        self.volume_alert_ratio = volume_alert_ratio
        self.stop_loss_warning = stop_loss_warning
        self.take_profit_warning = take_profit_warning

    async def collect(self, context: AgentContext) -> dict:
        """Thu thập bảng giá thời gian thực + nến + phân tích lịch sử"""
        if not context.watchlist:
            logger.warning("自选股列表为空，跳过盘中监测")
            return {"stocks": [], "stock_data": None}

        # SignalPack: đầu vào có cấu trúc thống nhất (quote/technical/position)
        stock_config = context.watchlist[0] if context.watchlist else None
        market = stock_config.market if stock_config else MarketCode.CN
        symbol = stock_config.symbol if stock_config else ""
        name = stock_config.name if stock_config else symbol

        # Chặn theo giờ giao dịch của đúng thị trường mà mã thuộc về (chứ không phải hễ có một thị trường nào mở là cho qua)
        if not self.bypass_market_hours and not is_market_trading(market):
            msg = f"当前{market_label(market)}非交易时段，已跳过执行"
            logger.info(f"{msg}: {symbol}")
            return {
                "stocks": [],
                "stock_data": None,
                "skip_reason": msg,
            }

        builder = SignalPackBuilder()
        packs = await builder.build_for_symbols(
            symbols=[(symbol, market, name)],
            include_news=True,
            news_hours=24,
            portfolio=context.portfolio,
            include_technical=True,
            include_capital_flow=True,
            include_events=True,
            events_days=3,
        )
        pack = packs.get(symbol)

        context_builder = ContextBuilder()
        context_pack = await context_builder.build_symbol_contexts(
            agent_name=self.name,
            context=context,
            packs=packs,
            realtime_hours=6,
            extended_hours=24,
            history_days=7,
            kline_days=60,
            persist_snapshot=True,
        )
        symbol_context = (context_pack.get("symbols", {}) or {}).get(symbol, {})
        quality_overview = context_pack.get("quality_overview", {}) or {}

        stock_data = pack.quote if pack and pack.quote else None

        kline_summary = pack.technical if pack else None

        # Lấy phân tích lịch sử (cấp thêm ngữ cảnh cho AI)
        daily_analysis = get_latest_analysis(
            agent_name="daily_report",
            stock_symbol="*",
            before_date=date.today(),
        )
        premarket_analysis = get_analysis(
            agent_name="premarket_outlook",
            stock_symbol="*",
            analysis_date=date.today(),
        )

        return {
            "stocks": [stock_data] if stock_data else [],
            "stock_data": stock_data,
            "kline_summary": kline_summary,
            "signal_pack": pack,
            "daily_analysis": daily_analysis.content if daily_analysis else None,
            "premarket_analysis": premarket_analysis.content
            if premarket_analysis
            else None,
            "symbol_context": symbol_context,
            "quality_overview": quality_overview,
            "timestamp": datetime.now().isoformat(),
        }

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """Dựng Prompt phân tích trong phiên"""
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

        # Hàm phụ: lấy số an toàn, None quy về giá trị mặc định
        def safe_num(value, default=0):
            return value if value is not None else default

        def format_num(value, precision=2):
            if value is None:
                return "N/A"
            return f"{value:.{precision}f}"

        stock: StockData | None = data.get("stock_data")
        if not stock:
            return system_prompt, "无股票数据"

        # Lấy thông tin vị thế của mọi tài khoản
        positions = context.portfolio.get_positions_for_stock(stock.symbol)
        style_labels = {"short": "短线", "swing": "波段", "long": "长线"}

        lines = []
        lines.append(f"## 时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

        # Giá cổ phiếu
        current_price = safe_num(stock.current_price)
        change_pct = safe_num(stock.change_pct)
        change_amount = safe_num(stock.change_amount)
        open_price = safe_num(stock.open_price)
        high_price = safe_num(stock.high_price)
        low_price = safe_num(stock.low_price)
        prev_close = safe_num(stock.prev_close)
        volume = safe_num(stock.volume)
        turnover = safe_num(stock.turnover)

        lines.append("## 股票行情")
        lines.append(f"- 股票：{stock.name}（{stock.symbol}）")
        lines.append(f"- 现价：{current_price:.2f}")
        lines.append(f"- 涨跌幅：{change_pct:+.2f}%")
        lines.append(f"- 涨跌额：{change_amount:+.2f}")
        lines.append(f"- 今开：{open_price:.2f}")
        lines.append(f"- 最高：{high_price:.2f}")
        lines.append(f"- 最低：{low_price:.2f}")
        lines.append(f"- 昨收：{prev_close:.2f}")
        if volume > 0:
            lines.append(f"- 成交量：{volume:.0f} 手")
        if turnover > 0:
            lines.append(f"- 成交额：{turnover / 10000:.0f} 万")

        # Ngưỡng hệ thống (giúp AI quyết định “cảnh báo / không cảnh báo” ổn định hơn)
        # Biến động giá chuyển sang ngưỡng thích ứng theo chính độ biến động của mã (ATR%), ngưỡng cố định chỉ làm cận dưới / dự phòng.
        from src.modules.strategy.intraday_event_gate import (
            DEFAULT_ATR_K,
            adaptive_price_threshold,
            is_abnormal_move,
        )

        kline_for_atr = data.get("kline_summary") or {}
        atr_pct = kline_for_atr.get("atr_pct")
        adaptive_threshold = adaptive_price_threshold(
            atr_pct, self.price_alert_threshold, DEFAULT_ATR_K
        )

        lines.append("\n## 系统阈值")
        if atr_pct is not None and atr_pct > 0:
            lines.append(
                f"- 价格异动：|涨跌幅| ≥ max(固定阈值 {self.price_alert_threshold:.1f}%, "
                f"{DEFAULT_ATR_K:g}×ATR%={atr_pct:.2f}%)={adaptive_threshold:.2f}%"
                f"（相对个股自身波动率自适应，固定阈值为下限）"
            )
        else:
            lines.append(
                f"- 价格异动：|涨跌幅| ≥ {self.price_alert_threshold:.1f}%"
                f"（ATR 不可用，回退固定阈值）"
            )
        lines.append(f"- 量能异动：量比 ≥ {self.volume_alert_ratio:.1f}")
        lines.append(f"- 止损预警：浮亏 ≤ {self.stop_loss_warning:.1f}%")
        lines.append(f"- 止盈提醒：浮盈 ≥ {self.take_profit_warning:.1f}%")
        price_hit = (
            "触发"
            if is_abnormal_move(
                change_pct,
                atr_pct,
                k=DEFAULT_ATR_K,
                fixed_threshold=self.price_alert_threshold,
            )
            else "未触发"
        )
        lines.append(f"- 当前涨跌幅：{change_pct:+.2f}%（{price_hit}）")

        symbol_ctx = data.get("symbol_context") or {}
        quality = (symbol_ctx.get("data_quality") or {})
        if quality:
            lines.append(
                f"- 上下文质量：{quality.get('score', 0)}（实时新闻 {quality.get('realtime_news_count', 0)} 条，扩展新闻 {quality.get('extended_news_count', 0)} 条，历史新闻 {quality.get('history_news_count', 0)} 条）"
            )

        layered_news = symbol_ctx.get("news") or {}
        realtime_news = layered_news.get("realtime") or []
        extended_news = layered_news.get("extended") or []
        history_news = layered_news.get("history") or []
        if realtime_news or extended_news or history_news:
            lines.append("\n## 新闻与事件上下文")
            chosen = realtime_news or extended_news or history_news
            for item in chosen[:3]:
                lines.append(
                    f"- [{item.get('time')}] {item.get('title')}（{item.get('source')}）"
                )
            hist_topic = (layered_news.get("history_topic") or {}).get("summary")
            if hist_topic:
                lines.append(f"- 历史新闻主题：{hist_topic}")

        kline_history = symbol_ctx.get("kline_history") or {}
        if kline_history.get("available"):
            lines.append("\n## 历史K线背景")
            lines.append(
                f"- 历史涨跌：5日{format_num(kline_history.get('ret_5d'), 1)}% / 20日{format_num(kline_history.get('ret_20d'), 1)}% / 60日{format_num(kline_history.get('ret_60d'), 1)}%"
            )
            if kline_history.get("volatility_20d") is not None:
                lines.append(
                    f"- 波动(20日标准差)：{format_num(kline_history.get('volatility_20d'), 2)}%"
                )
            if kline_history.get("breakout_state") and kline_history.get("breakout_state") != "none":
                lines.append(f"- 突破状态：{kline_history.get('breakout_state')}")

        # Nến và chỉ báo kỹ thuật
        kline = data.get("kline_summary")
        if kline and not kline.get("error"):
            lines.append("\n## 技术分析")

            # Xu hướng cơ sở
            lines.append(f"- 趋势：{kline.get('trend', 'N/A')}")
            lines.append(
                f"- 近5日：{kline.get('recent_5_up', 0)}涨{5 - kline.get('recent_5_up', 0)}跌"
            )
            lines.append(
                f"- 5日涨幅：{format_num(kline.get('change_5d'))}% | 20日涨幅：{format_num(kline.get('change_20d'))}%"
            )

            # MACD
            macd_info = f"MACD：{kline.get('macd_status', 'N/A')}"
            if kline.get("macd_cross_days"):
                macd_info += f"（{kline.get('macd_cross_days')}日前）"
            lines.append(f"- {macd_info}")

            # RSI
            rsi_status = kline.get("rsi_status")
            rsi6 = kline.get("rsi6")
            if rsi_status and rsi6 is not None:
                lines.append(f"- RSI(6)：{rsi6:.1f}（{rsi_status}）")

            # KDJ
            kdj_status = kline.get("kdj_status")
            kdj_k, kdj_d, kdj_j = (
                kline.get("kdj_k"),
                kline.get("kdj_d"),
                kline.get("kdj_j"),
            )
            if kdj_status and kdj_k is not None:
                lines.append(
                    f"- KDJ：K={kdj_k:.1f} D={kdj_d:.1f} J={kdj_j:.1f}（{kdj_status}）"
                )

            # Dải Bollinger
            boll_status = kline.get("boll_status")
            boll_upper, boll_lower = kline.get("boll_upper"), kline.get("boll_lower")
            if boll_status and boll_upper is not None:
                lines.append(
                    f"- 布林带：上轨={format_num(boll_upper)} 下轨={format_num(boll_lower)}（{boll_status}）"
                )

            # Sức khối lượng
            volume_trend = kline.get("volume_trend")
            volume_ratio = kline.get("volume_ratio")
            if volume_trend:
                vol_info = f"量能：{volume_trend}"
                if volume_ratio:
                    vol_info += f"（量比={volume_ratio:.2f}）"
                lines.append(f"- {vol_info}")
                if volume_ratio:
                    vol_hit = (
                        "触发" if volume_ratio >= self.volume_alert_ratio else "未触发"
                    )
                    lines.append(f"- 量比阈值判断：{vol_hit}")

            # Độ biến động (ATR): mốc dao động của chính mã đó, dùng để phân biệt "biến động bất thường vs dao động bình thường"
            atr_val = kline.get("atr")
            atr_pct_val = kline.get("atr_pct")
            if atr_pct_val is not None:
                atr_line = f"波动率：ATR={format_num(atr_val)}（ATR%={format_num(atr_pct_val)}%）"
                atr_line += (
                    f"，今日涨跌幅{change_pct:+.2f}% "
                    + (
                        "超出"
                        if abs(change_pct) >= adaptive_threshold
                        else "处于"
                    )
                    + f"自适应异动阈值{adaptive_threshold:.2f}%"
                )
                lines.append(f"- {atr_line}")

            # Đường trung bình
            lines.append(
                f"- MA5：{format_num(kline.get('ma5'))} | MA10：{format_num(kline.get('ma10'))} | MA20：{format_num(kline.get('ma20'))} | MA60：{format_num(kline.get('ma60'))}"
            )

        # Dòng tiền (chỉ cổ phiếu A, nếu có dữ liệu)
        pack = data.get("signal_pack")
        flow = getattr(pack, "capital_flow", None) if pack else None
        if (
            isinstance(flow, dict)
            and flow
            and not flow.get("error")
            and flow.get("status")
        ):
            try:
                inflow = float(flow.get("main_net_inflow") or 0)
                inflow_pct = float(flow.get("main_net_inflow_pct") or 0)
                inflow_str = (
                    f"{inflow / 1e8:+.2f}亿"
                    if abs(inflow) >= 1e8
                    else f"{inflow / 1e4:+.0f}万"
                )
                lines.append("\n## 资金面")
                lines.append(
                    f"- 资金：{flow.get('status')}，主力净流入{inflow_str}（{inflow_pct:+.1f}%）"
                )
                if flow.get("trend_5d") and flow.get("trend_5d") != "无数据":
                    lines.append(f"- 5日资金：{flow.get('trend_5d')}")
            except Exception:
                pass

            # Hỗ trợ / kháng cự nhiều tầng
            support_m, resistance_m = kline.get("support_m"), kline.get("resistance_m")
            if support_m and resistance_m:
                lines.append(
                    f"- 中期支撑：{format_num(support_m)} | 中期压力：{format_num(resistance_m)}"
                )

            support_s, resistance_s = kline.get("support_s"), kline.get("resistance_s")
            if support_s and resistance_s:
                lines.append(
                    f"- 短期支撑：{format_num(support_s)} | 短期压力：{format_num(resistance_s)}"
                )

            # Mẫu hình nến
            kline_pattern = kline.get("kline_pattern")
            if kline_pattern:
                lines.append(f"- K线形态：{kline_pattern}")

            # Biên dao động
            amplitude = kline.get("amplitude")
            amplitude_avg5 = kline.get("amplitude_avg5")
            if amplitude is not None:
                amp_info = f"今日振幅：{amplitude:.2f}%"
                if amplitude_avg5 is not None:
                    amp_info += f"（5日平均：{amplitude_avg5:.2f}%）"
                lines.append(f"- {amp_info}")

        # Tình hình vốn của tài khoản
        lines.append(f"\n## 账户资金")
        lines.append(f"- 总可用资金：{context.portfolio.total_available_funds:.0f} 元")
        for acc in context.portfolio.accounts:
            lines.append(f"  - {acc.name}：{acc.available_funds:.0f} 元")
        constraints = symbol_ctx.get("constraints") or {}
        if constraints:
            lines.append(
                f"- 单票仓位占比：{safe_num(constraints.get('single_position_ratio'), 0) * 100:.1f}%（{constraints.get('risk_budget_hint', 'normal')}）"
            )
        memory = symbol_ctx.get("memory") or {}
        if memory:
            lines.append(
                f"- 历史上下文记忆：近{memory.get('window_days', 30)}天质量均值{safe_num(memory.get('avg_quality_score'), 0):.1f}，趋势{memory.get('quality_trend', 'flat')}"
            )
            if memory.get("latest_history_topic"):
                lines.append(f"- 历史记忆主题：{memory.get('latest_history_topic')}")

        # Thông tin vị thế của từng tài khoản
        if positions:
            lines.append(f"\n## 持仓情况（共 {len(positions)} 个账户）")
            for i, pos in enumerate(positions, 1):
                cost_price = safe_num(pos.cost_price, 1)
                pnl_pct = (
                    (current_price - cost_price) / cost_price * 100
                    if cost_price > 0
                    else 0
                )
                style_label = style_labels.get(pos.trading_style, "波段")
                market_value = current_price * pos.quantity
                # Tìm tiền khả dụng của đúng tài khoản tương ứng
                acc_funds = 0
                for acc in context.portfolio.accounts:
                    if acc.id == pos.account_id:
                        acc_funds = acc.available_funds
                        break

                lines.append(f"\n### 持仓 {i}：{pos.account_name}")
                lines.append(f"- 交易风格：{style_label}")
                lines.append(f"- 成本价：{cost_price:.2f}")
                lines.append(f"- 持仓量：{pos.quantity} 股")
                lines.append(f"- 持仓市值：{market_value:.0f} 元")
                pnl_note = ""
                if pnl_pct <= self.stop_loss_warning:
                    pnl_note = "（触发止损预警）"
                elif pnl_pct >= self.take_profit_warning:
                    pnl_note = "（触发止盈提醒）"
                lines.append(f"- 浮动盈亏：{pnl_pct:+.1f}%{pnl_note}")
                lines.append(f"- 账户可用：{acc_funds:.0f} 元")
        else:
            lines.append("\n## 未持仓（仅关注）")
            lines.append(f"- 可用资金充足，可考虑建仓")

        # Ngữ cảnh từ phân tích lịch sử (giúp AI phán đoán tốt hơn)
        daily_analysis = data.get("daily_analysis")
        premarket_analysis = data.get("premarket_analysis")

        if daily_analysis or premarket_analysis:
            lines.append("\n## 历史分析参考")

            if daily_analysis:
                # Cắt lấy phần liên quan tới mã đang xét (tối đa 300 chữ)
                content = (
                    daily_analysis[:300] + "..."
                    if len(daily_analysis) > 300
                    else daily_analysis
                )
                lines.append(f"\n### 昨日盘后分析摘要")
                lines.append(content)

            if premarket_analysis:
                content = (
                    premarket_analysis[:300] + "..."
                    if len(premarket_analysis) > 300
                    else premarket_analysis
                )
                lines.append(f"\n### 今日盘前分析摘要")
                lines.append(content)

        lines.append("\n请结合技术分析、资金情况和历史分析，给出明确的操作建议。")

        user_content = "\n".join(lines)
        return system_prompt, user_content

    def _parse_suggestion(self, content: str) -> dict:
        """
        Đọc khuyến nghị thao tác từ phản hồi của AI

        Returns:
            {
                "action": "hold",  # buy/add/reduce/sell/hold/watch
                "action_label": "Nắm giữ",
                "signal": "...",
                "reason": "...",
                "should_alert": True
            }
        """
        result = {
            "action": "watch",
            "action_label": "观望",
            "signal": "",
            "reason": "",
            "should_alert": False,
        }

        # 1) Prefer JSON output (structured mode)
        obj = try_parse_action_json(content) or self._try_parse_loose_json(content)
        if obj:
            action = (obj.get("action") or "watch").strip()
            result["action"] = action
            result["action_label"] = (
                obj.get("action_label") or result["action_label"]
            ).strip()[:20]
            result["signal"] = (obj.get("signal") or "").strip()[:60]
            result["reason"] = (obj.get("reason") or "").strip()[:160]
            result["should_alert"] = action in {
                "buy",
                "add",
                "reduce",
                "sell",
                "alert",
                "avoid",
            }
            result["triggers"] = (
                obj.get("triggers") if isinstance(obj.get("triggers"), list) else []
            )
            result["invalidations"] = (
                obj.get("invalidations")
                if isinstance(obj.get("invalidations"), list)
                else []
            )
            result["risks"] = (
                obj.get("risks") if isinstance(obj.get("risks"), list) else []
            )
            return result

        # Kiểm tra xem có thuộc diện không cần cảnh báo không
        if "[无需提醒]" in content:
            result["should_alert"] = False
            result["action"] = "hold"
            result["action_label"] = "持有"
            return result

        # Bóc loại khuyến nghị (tìm trong toàn văn)
        for label, action in SUGGESTION_TYPES.items():
            if label in content:
                result["action"] = action
                result["action_label"] = label
                break

        # Bóc tín hiệu (hỗ trợ nhiều định dạng)
        signal_patterns = [
            r"「信号」\s*[:：]?\s*(.+?)(?=「|$|\n\n)",
            r"\*\*信号\*\*\s*[:：]?\s*(.+?)(?=\*\*|$|\n\n)",
            r"信号\s*[:：]\s*(.+?)(?=\n|$)",
        ]
        for pattern in signal_patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                result["signal"] = match.group(1).strip()[:50]
                break

        # Bóc nội dung khuyến nghị (hỗ trợ nhiều định dạng)
        suggest_patterns = [
            r"「建议」\s*[:：]?\s*(.+?)(?=「|$|\n\n)",
            r"\*\*建议\*\*\s*[:：]?\s*(.+?)(?=\*\*|$|\n\n)",
            r"建议\s*[:：]\s*(.+?)(?=\n|$)",
        ]
        for pattern in suggest_patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                suggest_text = match.group(1).strip()
                # Bóc loại hành động từ khuyến nghị
                for label, action in SUGGESTION_TYPES.items():
                    if label in suggest_text:
                        result["action"] = action
                        result["action_label"] = label
                        break
                # Nếu tín hiệu rỗng thì lấy luôn nội dung khuyến nghị làm tín hiệu
                if not result["signal"]:
                    result["signal"] = suggest_text[:50]
                break

        # Bóc lý do (hỗ trợ nhiều định dạng)
        reason_patterns = [
            r"「理由」\s*[:：]?\s*(.+?)(?=「|$|\n\n)",
            r"\*\*理由\*\*\s*[:：]?\s*(.+?)(?=\*\*|$|\n\n)",
            r"理由\s*[:：]\s*(.+?)(?=\n|$)",
        ]
        for pattern in reason_patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                result["reason"] = match.group(1).strip()[:100]
                break

        # Nếu không bóc được tín hiệu lẫn lý do thì thử lấy phần đầu của cả đoạn
        if not result["signal"] and not result["reason"]:
            # Dọn định dạng markdown rồi lấy 100 ký tự đầu
            clean_content = re.sub(r"\*\*|##|#", "", content).strip()
            # Bỏ qua các trường hợp không cần cảnh báo
            if not clean_content.startswith("[无需提醒]"):
                result["reason"] = clean_content[:100]

        # Chốt should_alert: chỉ cảnh báo khi hành động rõ ràng là “mở vị thế / gia tăng / giảm bớt / thanh lý”
        result["should_alert"] = result["action"] in {"buy", "add", "reduce", "sell"}
        return result

    def _try_parse_loose_json(self, text: str) -> dict | None:
        """Đọc JSON kiểu nới tay, hứng luôn các định dạng bất thường của mô hình."""
        raw = (text or "").strip()
        if not raw:
            return None

        # Tương thích dòng đầu là "json"
        lines = raw.splitlines()
        if lines and lines[0].strip().lower() == "json":
            raw = "\n".join(lines[1:]).strip()

        # Bỏ khối mã bao bởi dấu nháy
        if raw.startswith("```"):
            block_lines = raw.splitlines()
            if len(block_lines) >= 3 and block_lines[-1].strip().startswith("```"):
                raw = "\n".join(block_lines[1:-1]).strip()
                if raw.lower().startswith("json\n"):
                    raw = raw[5:].strip()

        # Ưu tiên phân tích trực tiếp, thất bại thì bóc mảnh đối tượng JSON đầu tiên
        try:
            obj = json.loads(raw)
        except Exception:
            m = re.search(r"\{[\s\S]*\}", raw)
            if not m:
                return None
            try:
                obj = json.loads(m.group(0))
            except Exception:
                return None

        if not isinstance(obj, dict):
            return None

        # Thiếu các trường then chốt thì không coi là JSON khuyến nghị
        keys = {"action", "action_label", "signal", "reason", "triggers", "invalidations", "risks"}
        if not any(k in obj for k in keys):
            return None
        return obj

    def _format_human_readable_content(
        self, stock: StockData, suggestion: dict, raw_content: str
    ) -> str:
        """Khi mô hình trả về JSON thì dựng nội dung thông báo cho người đọc được."""
        action_label = suggestion.get("action_label") or "观望"
        signal = suggestion.get("signal") or "无明显新信号"
        reason = suggestion.get("reason") or "请结合盘面与风控策略审慎判断。"
        triggers = (
            suggestion.get("triggers")
            if isinstance(suggestion.get("triggers"), list)
            else []
        )
        invalidations = (
            suggestion.get("invalidations")
            if isinstance(suggestion.get("invalidations"), list)
            else []
        )
        risks = (
            suggestion.get("risks") if isinstance(suggestion.get("risks"), list) else []
        )
        price = (
            f"{stock.current_price:.2f}" if getattr(stock, "current_price", None) else "N/A"
        )
        chg = f"{(stock.change_pct or 0):+.2f}%"
        lines = [
            f"{stock.name}（{stock.symbol}）",
            f"现价：{price}  涨跌：{chg}",
            f"建议：{action_label}",
            f"信号：{signal}",
            f"理由：{reason}",
        ]
        if triggers:
            lines.append("触发条件：")
            lines.extend([f"- {str(x)}" for x in triggers[:3]])
        if invalidations:
            lines.append("失效条件：")
            lines.extend([f"- {str(x)}" for x in invalidations[:3]])
        if risks:
            lines.append("风险提示：")
            lines.extend([f"- {str(x)}" for x in risks[:3]])
        # Nếu lần này không phải JSON thuần thì kèm tóm tắt nguyên văn ngắn để tiện đối chiếu
        if not (try_parse_action_json(raw_content) or self._try_parse_loose_json(raw_content)):
            brief = re.sub(r"\s+", " ", (raw_content or "").strip())[:200]
            if brief:
                lines.append(f"备注：{brief}")
        return "\n".join(lines)

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """AI phân tích rồi xét có cần cảnh báo không"""
        # Ngoài giờ giao dịch thì bỏ qua
        if data.get("skip_reason"):
            return AnalysisResult(
                agent_name=self.name,
                title=f"【{self.display_name}】跳过",
                content=data.get("skip_reason", "跳过执行"),
                raw_data={"skipped": True, **data},
            )

        stock: StockData | None = data.get("stock_data")

        if not stock:
            return AnalysisResult(
                agent_name=self.name,
                title=f"【{self.display_name}】无数据",
                content="未获取到股票数据",
                raw_data=data,
            )

        system_prompt, user_content = self.build_prompt(data, context)

        # In toàn bộ prompt để gỡ lỗi
        logger.info(f"=== Prompt for {stock.symbol} ===\n{user_content}")

        raw_content = await context.ai_client.chat(system_prompt, user_content)

        # In kết quả AI trả về
        logger.info(f"=== AI Response for {stock.symbol} ===\n{raw_content}")

        # Bóc khuyến nghị hành động
        suggestion = self._parse_suggestion(raw_content)
        content = raw_content
        analysis_date = (data.get("timestamp") or "")[:10] or datetime.now().strftime(
            "%Y-%m-%d"
        )
        quality_score = (
            (data.get("symbol_context") or {}).get("data_quality", {}).get("score")
        )
        # Khi đầu ra là JSON hoặc gần giống JSON thì chuyển thành văn bản thông báo đọc được, tránh đẩy JSON thô ra kênh
        if try_parse_action_json(raw_content) or self._try_parse_loose_json(raw_content):
            content = self._format_human_readable_content(stock, suggestion, raw_content)

        # Lưu vào kho khuyến nghị (kèm ngữ cảnh prompt)
        save_suggestion(
            stock_symbol=stock.symbol,
            stock_name=stock.name,
            action=suggestion["action"],
            action_label=suggestion["action_label"],
            signal=suggestion.get("signal", ""),
            reason=suggestion.get("reason", ""),
            agent_name=self.name,
            agent_label=self.display_name,
            expires_hours=6,  # Khuyến nghị trong phiên có hiệu lực 6 giờ
            prompt_context=user_content,  # Lưu ngữ cảnh prompt
            ai_response=raw_content,  # Lưu phản hồi gốc của AI
            stock_market=stock.market.value,
            meta={
                "quote": {
                    "current_price": stock.current_price,
                    "change_pct": stock.change_pct,
                },
                "kline_meta": {
                    "computed_at": (data.get("kline_summary") or {}).get("computed_at"),
                    "asof": (data.get("kline_summary") or {}).get("asof"),
                },
                "event_gate": data.get("event_gate"),
                "analysis_date": analysis_date,
                "context_quality_score": quality_score,
                "plan": {
                    "triggers": suggestion.get("triggers")
                    if isinstance(suggestion, dict)
                    else [],
                    "invalidations": suggestion.get("invalidations")
                    if isinstance(suggestion, dict)
                    else [],
                    "risks": suggestion.get("risks")
                    if isinstance(suggestion, dict)
                    else [],
                },
            },
        )
        prediction_group_id = str(uuid.uuid4())
        for horizon in (1, 5):
            save_agent_prediction_outcome(
                agent_name=self.name,
                stock_symbol=stock.symbol,
                stock_market=stock.market.value,
                prediction_date=analysis_date,
                horizon_days=horizon,
                prediction_group_id=prediction_group_id,
                action=suggestion.get("action") or "watch",
                action_label=suggestion.get("action_label") or "观望",
                confidence=(float(quality_score) / 100.0)
                if quality_score is not None
                else None,
                trigger_price=getattr(stock, "current_price", None),
                meta={
                    "source": "intraday_monitor",
                    "reason": suggestion.get("reason", ""),
                    "signal": suggestion.get("signal", ""),
                },
            )

        save_agent_context_run(
            agent_name=self.name,
            stock_symbol=stock.symbol,
            analysis_date=analysis_date,
            context_payload={
                "symbol_context": data.get("symbol_context") or {},
                "quality_overview": data.get("quality_overview") or {},
            },
            quality={"score": quality_score or 0},
        )

        # Dựng tiêu đề
        title = f"【{self.display_name}】{stock.name} {stock.change_pct:+.2f}%"

        # Kèm thông tin mô hình AI
        if context.model_label:
            content = content.rstrip() + f"\n\n---\nAI: {context.model_label}"

        # Liên động khi tăng / giảm đột ngột: chạm ngưỡng thì kích hoạt bất đồng bộ phân tích chuyên sâu TradingAgents (mặc định tắt)
        try:
            from src.modules.automation.tradingagents.operations import try_auto_trigger
            try_auto_trigger(stock, source_agent=self.name)
        except Exception:
            logger.exception("TA 联动触发失败,继续返回 intraday 结果")

        return AnalysisResult(
            agent_name=self.name,
            title=title,
            content=content,
            raw_data={
                "stock": {
                    "symbol": stock.symbol,
                    "name": stock.name,
                    "current_price": stock.current_price,
                    "change_pct": stock.change_pct,
                },
                "suggestion": suggestion,
                "should_alert": suggestion["should_alert"],
                "kline_summary": data.get("kline_summary"),
                "symbol_context": data.get("symbol_context") or {},
                "quality_overview": data.get("quality_overview") or {},
                **data,
            },
        )

    async def should_notify(self, result: AnalysisResult) -> bool:
        """Kiểm tra xem có cần thông báo không"""
        # Kết quả bị bỏ qua thì không gửi thông báo
        if result.raw_data.get("skipped"):
            return False

        # AI kết luận là không cần cảnh báo
        if not result.raw_data.get("should_alert", True):
            logger.info(
                f"AI 判断无需提醒: {result.raw_data.get('stock', {}).get('symbol')}"
            )
            return False

        stock_data = result.raw_data.get("stock")
        if not stock_data:
            return False

        symbol = stock_data.get("symbol")
        if not symbol:
            return False

        # Kiểm tra giãn nhịp (chế độ kiểm thử có thể bỏ qua)
        if not self.bypass_throttle:
            if not self._check_throttle(symbol):
                logger.info(
                    f"通知节流: {symbol} 在 {self.throttle_minutes} 分钟内已通知"
                )
                return False
        else:
            logger.info(f"跳过节流检查（测试模式）: {symbol}")

        return True

    def _check_throttle(self, symbol: str) -> bool:
        """Kiểm tra xem có gửi thông báo được không (chưa bị tiết lưu)"""
        from src.platform.persistence.database import SessionLocal
        from src.platform.persistence.models import NotifyThrottle

        db = SessionLocal()
        try:
            record = (
                db.query(NotifyThrottle)
                .filter(
                    NotifyThrottle.agent_name == self.name,
                    NotifyThrottle.stock_symbol == symbol,
                )
                .first()
            )

            if not record:
                return True

            # So sánh theo UTC, tránh sự cố khi múi giờ của container / môi trường triển khai thay đổi
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            threshold = now - timedelta(minutes=self.throttle_minutes)
            last = record.last_notify_at
            if last and last.tzinfo is not None:
                last = last.astimezone(timezone.utc).replace(tzinfo=None)
            return (last or datetime.fromtimestamp(0)) < threshold
        finally:
            db.close()

    def _update_throttle(self, symbol: str):
        """Cập nhật bản ghi tiết lưu"""
        from src.platform.persistence.database import SessionLocal
        from src.platform.persistence.models import NotifyThrottle

        db = SessionLocal()
        try:
            record = (
                db.query(NotifyThrottle)
                .filter(
                    NotifyThrottle.agent_name == self.name,
                    NotifyThrottle.stock_symbol == symbol,
                )
                .first()
            )

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if record:
                # Kiểm tra đã sang ngày mới chưa
                if record.last_notify_at.date() < now.date():
                    record.notify_count = 1
                else:
                    record.notify_count += 1
                record.last_notify_at = now
            else:
                db.add(
                    NotifyThrottle(
                        agent_name=self.name,
                        stock_symbol=symbol,
                        last_notify_at=now,
                        notify_count=1,
                    )
                )

            db.commit()
        finally:
            db.close()

    async def run_single(
        self, context: AgentContext, stock_symbol: str
    ) -> AnalysisResult | None:
        """
        Chạy ở chế độ từng mã: chỉ phân tích đúng một mã đã chỉ định

        Dùng cho tình huống giám sát thời gian thực, mỗi mã phân tích và thông báo riêng
        """
        # Lọc giữ lại đúng các mã được chỉ định
        original_watchlist = context.config.watchlist
        context.config.watchlist = [
            s for s in original_watchlist if s.symbol == stock_symbol
        ]

        if not context.config.watchlist:
            return None

        try:
            data = await self.collect(context)
            if not data.get("stock_data"):
                return None

            # Cổng sự kiện chỉ đóng vai trò tín hiệu ngữ cảnh, không chặn AI phân tích.
            # Định hướng sản phẩm: khuyến nghị cứ làm mới liên tục, còn thông báo thì để should_alert + giãn nhịp lọc bớt nhiễu.
            if self.event_only:
                try:
                    from src.modules.strategy.intraday_event_gate import check_and_update

                    stock = data.get("stock_data")
                    kline_summary = data.get("kline_summary")
                    decision = check_and_update(
                        symbol=stock_symbol,
                        change_pct=getattr(stock, "change_pct", None),
                        volume_ratio=(kline_summary or {}).get("volume_ratio"),
                        kline_summary=kline_summary,
                        price_threshold=self.price_alert_threshold,
                        volume_threshold=self.volume_alert_ratio,
                    )
                    data["event_gate"] = {
                        "reasons": decision.reasons,
                        "should_analyze": bool(decision.should_analyze),
                    }
                except Exception as e:
                    logger.debug(f"事件门禁异常，继续分析: {e}")

            result = await self.analyze(context, data)

            if getattr(context, "suppress_notify", False):
                result.raw_data["notified"] = False
                result.raw_data["notify_skipped"] = "suppressed"
                return result

            if await self.should_notify(result):
                notify_result = await context.notifier.notify_with_result(
                    result.title,
                    result.content,
                    result.images,
                )
                notified = bool(notify_result.get("success"))
                result.raw_data["notified"] = notified
                if notified:
                    logger.info(
                        f"Agent [{self.display_name}] 通知已发送: {stock_symbol}"
                    )
                    if not self.bypass_throttle:
                        self._update_throttle(stock_symbol)
                else:
                    notify_error = notify_result.get("error") or "未知错误"
                    result.raw_data["notify_error"] = notify_error
                    logger.error(
                        f"Agent [{self.display_name}] 通知发送失败: {stock_symbol} - {notify_error}"
                    )
            else:
                result.raw_data["notified"] = False

            return result
        finally:
            context.config.watchlist = original_watchlist
