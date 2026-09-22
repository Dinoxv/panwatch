"""资金流向采集器 - 经 marketdata 包统一接入"""
from dataclasses import dataclass

from src.platform.marketdata.collectors.market_http import TTLCache
from src.platform.marketdata.models import MarketCode

# 资金流为日级数据、变动慢:中等 TTL 缓存,避免每轮重复拉。
_FLOW_CACHE = TTLCache(default_ttl_sec=600.0)


@dataclass
class CapitalFlow:
    """资金流向数据"""
    symbol: str
    name: str

    # 今日资金流（单位：元）
    main_net_inflow: float      # Dòng tiền lớn vào ròng
    main_net_inflow_pct: float  # Tỷ trọng dòng tiền lớn vào ròng
    super_net_inflow: float     # Lệnh siêu lớn vào ròng
    big_net_inflow: float       # Lệnh lớn vào ròng
    mid_net_inflow: float       # Lệnh vừa vào ròng
    small_net_inflow: float     # Lệnh nhỏ vào ròng

    # 5日资金流
    main_net_5d: float | None = None  # Dòng tiền lớn vào ròng 5 phiên


def get_market_data():
    """惰性导入,避免模块加载时的循环依赖(便于测试 monkeypatch)。"""
    from src.platform.marketdata.marketdata_client import get_market_data as _g
    return _g()


class CapitalFlowCollector:
    """资金流向采集器"""

    def __init__(self, market: MarketCode):
        self.market = market

    def get_capital_flow(self, symbol: str) -> CapitalFlow | None:
        """获取单只股票的资金流向(经 marketdata 包统一接入 + TTL缓存)。"""
        cache_key = f"{self.market.value}:{symbol}"
        cached = _FLOW_CACHE.get(cache_key)
        if cached is not None:
            return cached

        md_cf = get_market_data().capital_flow(symbol, market=self.market.value)
        if md_cf is None:
            return None
        capital_flow = CapitalFlow(
            symbol=md_cf.symbol,
            name=md_cf.name,
            main_net_inflow=md_cf.main_net_inflow,
            main_net_inflow_pct=md_cf.main_net_inflow_pct,
            super_net_inflow=md_cf.super_net_inflow,
            big_net_inflow=md_cf.big_net_inflow,
            mid_net_inflow=md_cf.mid_net_inflow,
            small_net_inflow=md_cf.small_net_inflow,
            main_net_5d=md_cf.main_net_5d,
        )
        _FLOW_CACHE.set(cache_key, capital_flow)
        return capital_flow

    def get_capital_flow_summary(self, symbol: str) -> dict:
        """获取资金流向摘要（用于 prompt）"""
        flow = self.get_capital_flow(symbol)

        if not flow:
            return {"error": "无资金流向数据"}

        # 判断资金状态
        if flow.main_net_inflow > 0:
            if flow.main_net_inflow_pct > 10:
                status = "主力大幅流入"
            elif flow.main_net_inflow_pct > 5:
                status = "主力明显流入"
            else:
                status = "主力小幅流入"
        elif flow.main_net_inflow < 0:
            if flow.main_net_inflow_pct < -10:
                status = "主力大幅流出"
            elif flow.main_net_inflow_pct < -5:
                status = "主力明显流出"
            else:
                status = "主力小幅流出"
        else:
            status = "主力资金平衡"

        # 5日趋势
        trend_5d = "无数据"
        if flow.main_net_5d is not None:
            if flow.main_net_5d > 0:
                trend_5d = f"5日净流入{flow.main_net_5d/1e8:.2f}亿"
            else:
                trend_5d = f"5日净流出{abs(flow.main_net_5d)/1e8:.2f}亿"

        return {
            "status": status,
            "main_net_inflow": flow.main_net_inflow,
            "main_net_inflow_pct": flow.main_net_inflow_pct,
            "super_net_inflow": flow.super_net_inflow,
            "big_net_inflow": flow.big_net_inflow,
            "mid_net_inflow": flow.mid_net_inflow,
            "small_net_inflow": flow.small_net_inflow,
            "trend_5d": trend_5d,
        }
