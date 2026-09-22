"""共享上下文层三项增强的单元测试。

覆盖:
- ④ 注入最近一次 TradingAgents 深度结论(ta_verdict)
- ② 个股相对大盘强度(relative_strength)
- ① 重要公告全文 + 头部新闻保留更多正文(content_fulltext)

所有外部取数(K线 / DB / 东财全文接口)均以 monkeypatch 打桩,
保证用例无网络依赖且验证 fail-soft 行为。
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from src.modules.research import analysis_history, context_builder
from src.modules.research.context_builder import ContextBuilder
from src.platform.marketdata.models import MarketCode


# --------------------------------------------------------------------------- #
# ④ Nạp kết luận chuyên sâu của TradingAgents
# --------------------------------------------------------------------------- #


def _make_history_row(*, analysis_date: str, suggestion: dict, content: str = ""):
    """构造一个伪 AnalysisHistory ORM 行(只含被读取的字段)。"""
    return SimpleNamespace(
        agent_name="tradingagents",
        stock_symbol="600519",
        analysis_date=analysis_date,
        content=content,
        raw_data={"suggestion": suggestion, "rating": suggestion.get("rating_raw")},
    )


def test_ta_verdict_recent_row_injected(monkeypatch):
    """N 天内有 TA 深度记录时,抽取出紧凑结论(评级/一句话/日期/age)。"""
    today = date.today()
    row = _make_history_row(
        analysis_date=today.strftime("%Y-%m-%d"),
        suggestion={
            "action": "buy",
            "action_label": "买入",
            "rating_raw": "buy",
            "reason": "基本面与技术面共振," * 30,  # Vượt xa 120 chữ nên phải cắt
        },
    )

    monkeypatch.setattr(
        analysis_history,
        "get_latest_ta_verdict_row",
        lambda symbol, within_days=14, today=None: row,
    )

    verdict = analysis_history.get_latest_ta_verdict("600519", within_days=14)
    assert verdict is not None
    assert verdict["action_label"] == "买入"
    assert verdict["rating"] == "buy"
    assert verdict["date"] == today.strftime("%Y-%m-%d")
    assert verdict["age_days"] == 0
    # Một câu cần được làm sạch + cắt về khoảng 120 chữ
    assert isinstance(verdict["one_liner"], str)
    assert 0 < len(verdict["one_liner"]) <= 130


def test_ta_verdict_includes_today(monkeypatch):
    """当天的 TA 记录也必须被纳入(age_days == 0)。"""
    today = date.today()
    captured = {}

    def fake_query(agent_name, stock_symbol, before_date=None):
        captured["before_date"] = before_date
        return _make_history_row(
            analysis_date=today.strftime("%Y-%m-%d"),
            suggestion={"action_label": "增持", "rating_raw": "overweight", "reason": "放量突破"},
        )

    monkeypatch.setattr(analysis_history, "get_latest_analysis", fake_query)

    row = analysis_history.get_latest_ta_verdict_row("600519", within_days=14)
    assert row is not None
    # before_date bắt buộc là "hôm nay + 1 ngày" để bao gồm cả hôm nay
    assert captured["before_date"] == today + timedelta(days=1)


def test_ta_verdict_too_old_returns_none(monkeypatch):
    """超过 N 天的记录被丢弃,返回 None。"""
    today = date.today()
    old = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    row = _make_history_row(
        analysis_date=old,
        suggestion={"action_label": "卖出", "rating_raw": "sell", "reason": "趋势走坏"},
    )
    monkeypatch.setattr(
        analysis_history,
        "get_latest_ta_verdict_row",
        lambda symbol, within_days=14, today=None: row,
    )
    verdict = analysis_history.get_latest_ta_verdict("600519", within_days=14)
    assert verdict is None


def test_ta_verdict_no_row_returns_none(monkeypatch):
    """没有任何 TA 记录时返回 None,不抛异常。"""
    monkeypatch.setattr(
        analysis_history,
        "get_latest_ta_verdict_row",
        lambda symbol, within_days=14, today=None: None,
    )
    assert analysis_history.get_latest_ta_verdict("600519") is None


def test_ta_verdict_parse_error_failsoft(monkeypatch):
    """raw_data 结构异常(parse error)时 fail-soft 返回 None。"""
    bad = SimpleNamespace(
        analysis_date=date.today().strftime("%Y-%m-%d"),
        content="x",
        raw_data="not-a-dict",  # Gây ngoại lệ ở .get
    )
    monkeypatch.setattr(
        analysis_history,
        "get_latest_ta_verdict_row",
        lambda symbol, within_days=14, today=None: bad,
    )
    assert analysis_history.get_latest_ta_verdict("600519") is None


# --------------------------------------------------------------------------- #
# ② Sức mạnh tương đối so với thị trường chung
# --------------------------------------------------------------------------- #


def test_relative_strength_computes_excess():
    """股票收益 - 指数收益 = 超额收益,字段齐全。"""
    cb = ContextBuilder()
    kline_history = {"available": True, "ret_5d": 3.0, "ret_20d": 10.0}
    index_ctx = {"available": True, "ret_5d": 1.0, "ret_20d": 4.0}

    rs = cb._compute_relative_strength(
        market=MarketCode.CN,
        kline_history=kline_history,
        index_ctx=index_ctx,
    )
    assert rs is not None
    assert rs["stock_5d"] == 3.0
    assert rs["index_5d"] == 1.0
    assert rs["excess_5d"] == pytest.approx(2.0)
    assert rs["excess_20d"] == pytest.approx(6.0)
    assert rs["index_label"]  # Nhãn khác rỗng (CSI 300...)


def test_relative_strength_missing_index_returns_none():
    """指数数据缺失时返回 None(fail-soft)。"""
    cb = ContextBuilder()
    rs = cb._compute_relative_strength(
        market=MarketCode.CN,
        kline_history={"available": True, "ret_5d": 3.0, "ret_20d": 10.0},
        index_ctx={"available": False},
    )
    assert rs is None


def test_relative_strength_missing_stock_returns_none():
    """个股 K线缺失时返回 None。"""
    cb = ContextBuilder()
    rs = cb._compute_relative_strength(
        market=MarketCode.HK,
        kline_history={"available": False},
        index_ctx={"available": True, "ret_5d": 1.0, "ret_20d": 4.0},
    )
    assert rs is None


def test_index_symbol_map_covers_markets():
    """三大市场都映射到一个指数代码 + 中文标签。"""
    cb = ContextBuilder()
    for mkt in (MarketCode.CN, MarketCode.HK, MarketCode.US):
        sym, label = cb._index_for_market(mkt)
        assert sym
        assert label


def test_index_returns_cached_once_per_build(monkeypatch):
    """同一次构建内,同市场指数只取一次(命中本地缓存,不重复请求)。"""
    cb = ContextBuilder()
    calls = {"n": 0}

    def fake_fetch(symbol, market):
        calls["n"] += 1
        return {"available": True, "ret_5d": 1.0, "ret_20d": 2.0}

    monkeypatch.setattr(cb, "_fetch_index_context", fake_fetch)

    first = cb._get_index_context(MarketCode.CN)
    second = cb._get_index_context(MarketCode.CN)
    assert first is second
    assert calls["n"] == 1


# --------------------------------------------------------------------------- #
# ① Toàn văn công bố thông tin + giữ phần thân của tin đầu
# --------------------------------------------------------------------------- #


def test_announcement_fulltext_attached_to_important_events(monkeypatch):
    """重要公告(importance>=2)被附加 content_fulltext 且截断。"""
    cb = ContextBuilder()
    events = [
        {"title": "重大资产重组", "external_id": "AN001", "importance": 3, "source": "eastmoney"},
        {"title": "年度报告", "external_id": "AN002", "importance": 3, "source": "eastmoney"},
        {"title": "日常关联交易", "external_id": "AN003", "importance": 0, "source": "eastmoney"},
    ]

    def fake_fetch(art_code):
        return "全文内容" * 600  # Vượt xa 1000 chữ nên phải cắt

    monkeypatch.setattr(context_builder, "fetch_announcement_fulltext", fake_fetch)

    enriched = cb._enrich_events_fulltext(events, top_k=3)
    # Hai bản ghi quan trọng có kèm toàn văn
    assert "content_fulltext" in enriched[0]
    assert "content_fulltext" in enriched[1]
    assert len(enriched[0]["content_fulltext"]) <= 1100
    # Bản ghi không quan trọng thì không kèm toàn văn (chỉ còn tiêu đề)
    assert "content_fulltext" not in enriched[2]


def test_announcement_fulltext_fetch_failure_keeps_headline(monkeypatch):
    """全文抓取失败时不崩溃,保留标题(无 content_fulltext)。"""
    cb = ContextBuilder()
    events = [
        {"title": "重大事项", "external_id": "AN009", "importance": 3, "source": "eastmoney"},
    ]

    def boom(art_code):
        raise RuntimeError("network down")

    monkeypatch.setattr(context_builder, "fetch_announcement_fulltext", boom)

    enriched = cb._enrich_events_fulltext(events, top_k=3)
    assert enriched[0]["title"] == "重大事项"
    assert "content_fulltext" not in enriched[0]


def test_announcement_fulltext_empty_result_no_attach(monkeypatch):
    """全文接口返回空时不附加字段。"""
    cb = ContextBuilder()
    events = [{"title": "重组", "external_id": "AN010", "importance": 2}]
    monkeypatch.setattr(context_builder, "fetch_announcement_fulltext", lambda art_code: "")
    enriched = cb._enrich_events_fulltext(events, top_k=3)
    assert "content_fulltext" not in enriched[0]


def test_fetch_announcement_fulltext_parses_notice_content(monkeypatch):
    """东财 content API 返回 data.notice_content 时抽取纯文本。"""
    from src.platform.marketdata.collectors import events_collector

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"notice_content": "  这是公告正文  "}}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            return _Resp()

    monkeypatch.setattr(events_collector.httpx, "Client", _Client)
    text = events_collector.fetch_announcement_fulltext("AN123")
    assert text == "这是公告正文"


def test_fetch_announcement_fulltext_failsoft(monkeypatch):
    """content API 抛错时返回空串,不抛异常。"""
    from src.platform.marketdata.collectors import events_collector

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            raise RuntimeError("boom")

    monkeypatch.setattr(events_collector.httpx, "Client", _Client)
    assert events_collector.fetch_announcement_fulltext("AN999") == ""


def test_news_top_items_retain_more_content():
    """头部新闻(有正文的)放宽到 max_chars,非头部条目维持原样不动。"""
    cb = ContextBuilder()
    long_content = "新闻正文" * 400  # Khoảng 1600 chữ
    short_content = "短讯" * 5  # Phần thân ngắn đã bị tầng thu thập cắt bớt
    news = [
        {"title": "头条", "content": long_content},
        {"title": "次条", "content": long_content},
        {"title": "第三条", "content": short_content},
    ]
    out = cb._retain_news_content(news, top_k=2, max_chars=800)
    # Hai bản ghi đầu được nới lên 800
    assert len(out[0]["content"]) == 800
    assert len(out[1]["content"]) == 800
    # Bản ghi thứ ba không nằm trong top_k nên giữ nguyên (hàm này chỉ nới phần đầu, không cắt thêm)
    assert out[2]["content"] == short_content


def test_news_retain_no_content_failsoft():
    """没有正文字段的新闻不报错,保持原样。"""
    cb = ContextBuilder()
    news = [{"title": "仅标题"}, {"title": "仅标题2"}]
    out = cb._retain_news_content(news, top_k=2, max_chars=800)
    assert "content" not in out[0]
    assert out[0]["title"] == "仅标题"


# --------------------------------------------------------------------------- #
# Tích hợp: build_symbol_contexts ghi ba trường mới vào payload
# --------------------------------------------------------------------------- #


class _FakePortfolio:
    def get_aggregated_position(self, symbol):
        return None

    accounts: list = []
    total_available_funds = 0
    total_cost = 0


class _FakeContext:
    def __init__(self, watchlist):
        self.watchlist = watchlist
        self.portfolio = _FakePortfolio()


def test_build_symbol_contexts_injects_new_keys(monkeypatch):
    """端到端:payload 同时带 ta_verdict / relative_strength / events.content_fulltext。"""
    stock = SimpleNamespace(symbol="600519", market=MarketCode.CN, name="贵州茅台")
    context = _FakeContext([stock])

    events_snapshot = SimpleNamespace(
        days=7,
        items=[
            {
                "title": "重大资产重组预案",
                "external_id": "ANX",
                "importance": 3,
                "source": "eastmoney",
                "symbols": ["600519"],
            }
        ],
    )
    pack = SimpleNamespace(
        quote=object(),
        technical={"trend": "多头"},
        news=SimpleNamespace(items=[]),
        events=events_snapshot,
    )
    packs = {"600519": pack}

    # Nến của mã riêng lẻ và nến của chỉ số: phân biệt bằng thị trường (chỉ số đi theo CSI 300 của CN).
    def fake_kline(*, symbol, market, lookback_days=120):
        if symbol == "600519":
            return {"available": True, "ret_5d": 5.0, "ret_20d": 12.0, "trend": "多头"}
        # Chỉ số (000300)
        return {"available": True, "ret_5d": 2.0, "ret_20d": 4.0}

    # Đặt stub trong không gian tên của context_builder (module này import thẳng hai ký hiệu đó)
    monkeypatch.setattr(context_builder, "build_kline_history_context", fake_kline)
    # ② Chỉ số đi qua _fetch_index_context (get_index_klines lấy thẳng bằng secid tường minh), nên stub luôn giá trị trả về của nó
    monkeypatch.setattr(
        ContextBuilder, "_fetch_index_context",
        lambda self, symbol, market: {"available": True, "ret_5d": 2.0, "ret_20d": 4.0},
    )
    monkeypatch.setattr(
        context_builder, "fetch_announcement_fulltext", lambda art_code: "重组全文细节" * 50
    )
    monkeypatch.setattr(
        context_builder,
        "get_latest_ta_verdict",
        lambda symbol, within_days=14: {
            "rating": "buy",
            "action_label": "买入",
            "one_liner": "基本面强劲,维持买入",
            "date": date.today().strftime("%Y-%m-%d"),
            "age_days": 0,
        },
    )
    # Tin lịch sử / lưu ảnh chụp / ảnh chụp chủ đề đều được stub để khỏi chạm cơ sở dữ liệu
    monkeypatch.setattr(ContextBuilder, "_load_history_news", staticmethod(lambda *a, **k: []))
    monkeypatch.setattr(context_builder, "save_stock_context_snapshot", lambda **k: None)
    monkeypatch.setattr(context_builder, "save_news_topic_snapshot", lambda **k: None)

    cb = ContextBuilder()
    result = asyncio.run(
        cb.build_symbol_contexts(
            agent_name="premarket_outlook",
            context=context,
            packs=packs,
            persist_snapshot=False,
        )
    )
    payload = result["symbols"]["600519"]

    # ④ Kết luận của TA
    assert payload["ta_verdict"]["action_label"] == "买入"
    # ② Sức mạnh tương đối: 5,0 - 2,0 = 3,0
    rs = payload["relative_strength"]
    assert rs is not None
    assert rs["excess_5d"] == pytest.approx(3.0)
    assert rs["excess_20d"] == pytest.approx(8.0)
    assert rs["index_label"] == "沪深300"
    # ① Toàn văn công bố thông tin
    assert "content_fulltext" in payload["events"][0]
    assert len(payload["events"][0]["content_fulltext"]) <= 1000


def test_build_symbol_contexts_failsoft_when_index_missing(monkeypatch):
    """指数取数失败时 relative_strength=None,且整体不崩溃。"""
    stock = SimpleNamespace(symbol="00700", market=MarketCode.HK, name="腾讯控股")
    context = _FakeContext([stock])
    pack = SimpleNamespace(
        quote=object(),
        technical={"trend": "多头"},
        news=SimpleNamespace(items=[]),
        events=SimpleNamespace(days=7, items=[]),
    )

    def fake_kline(*, symbol, market, lookback_days=120):
        if symbol == "00700":
            return {"available": True, "ret_5d": 3.0, "ret_20d": 9.0}
        return {"available": False}  # Không lấy được chỉ số

    monkeypatch.setattr(context_builder, "build_kline_history_context", fake_kline)
    # Lấy chỉ số thất bại → _fetch_index_context trả available False (mang tính tất định, không phụ thuộc mạng)
    monkeypatch.setattr(
        ContextBuilder, "_fetch_index_context", lambda self, symbol, market: {"available": False}
    )
    monkeypatch.setattr(context_builder, "get_latest_ta_verdict", lambda symbol, within_days=14: None)
    monkeypatch.setattr(ContextBuilder, "_load_history_news", staticmethod(lambda *a, **k: []))

    cb = ContextBuilder()
    result = asyncio.run(
        cb.build_symbol_contexts(
            agent_name="daily_report",
            context=context,
            packs={"00700": pack},
            persist_snapshot=False,
        )
    )
    payload = result["symbols"]["00700"]
    assert payload["relative_strength"] is None
    assert payload["ta_verdict"] is None
