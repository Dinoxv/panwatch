from marketdata.cache import TTLCache
from marketdata.defaults import InMemoryMetricsSink
from marketdata.engine import Engine
from marketdata.ports import SourceConfig
from marketdata.types import Request


class FakeVendor:
    def __init__(self, name, behavior):
        self.name, self.behavior, self.supports_markets = name, behavior, set()

    def fetch(self, symbols, config):
        if self.behavior == "raise":
            raise RuntimeError("boom")
        if self.behavior == "empty":
            return []
        return [{"symbol": symbols[0].code, "v": self.name}]


class FakeConfig:
    def __init__(self, srcs):
        self._srcs = srcs

    def sources_for(self, datatype, market):
        return list(self._srcs)


def _engine(vendors, srcs, metrics=None):
    return Engine(
        datatype="quote", vendors=vendors, config=FakeConfig(srcs),
        metrics=metrics or InMemoryMetricsSink(), cache=TTLCache(5.0), default_ttl=5.0,
    )


def _req():
    return Request(symbols=("600519",), market="CN")


def test_first_success_wins():
    e = _engine({"a": FakeVendor("a", "ok"), "b": FakeVendor("b", "ok")},
                [SourceConfig(vendor="a", priority=1), SourceConfig(vendor="b", priority=2)])
    r = e.fetch(_req())
    assert r.ok and r.vendor == "a" and r.data[0]["v"] == "a"


def test_failover_empty_then_raise_then_ok():
    e = _engine({"a": FakeVendor("a", "empty"), "b": FakeVendor("b", "raise"), "c": FakeVendor("c", "ok")},
                [SourceConfig(vendor="a", priority=1), SourceConfig(vendor="b", priority=2), SourceConfig(vendor="c", priority=3)])
    r = e.fetch(_req())
    assert r.ok and r.vendor == "c"


def test_all_fail_returns_not_ok():
    e = _engine({"a": FakeVendor("a", "empty")}, [SourceConfig(vendor="a", priority=1)])
    assert e.fetch(_req()).ok is False


def test_market_filter_skips_unsupported():
    v = FakeVendor("a", "ok")
    v.supports_markets = {"US"}   # Không hỗ trợ CN
    assert _engine({"a": v}, [SourceConfig(vendor="a", priority=1)]).fetch(_req()).ok is False


def test_cache_hit_skips_second_call():
    v = FakeVendor("a", "ok")
    calls = {"n": 0}
    inner = v.fetch
    v.fetch = lambda s, c: (calls.__setitem__("n", calls["n"] + 1), inner(s, c))[1]
    e = _engine({"a": v}, [SourceConfig(vendor="a", priority=1)])
    e.fetch(_req()); e.fetch(_req())
    assert calls["n"] == 1


def test_metrics_recorded():
    m = InMemoryMetricsSink()
    _engine({"a": FakeVendor("a", "ok")}, [SourceConfig(vendor="a", priority=1)], metrics=m).fetch(_req())
    assert m.snapshot()["a"]["success_rate"] == 1.0


def test_priority_resort_when_config_unsorted():
    e = _engine({"a": FakeVendor("a", "ok"), "b": FakeVendor("b", "ok")},
                [SourceConfig(vendor="b", priority=2), SourceConfig(vendor="a", priority=1)])
    r = e.fetch(_req())
    assert r.ok and r.vendor == "a"


def test_min_count_prefers_first_sufficient():
    e = _engine({"a": FakeVendor("a", "ok"), "b": FakeVendor("b", "ok")},
                [SourceConfig(vendor="a", priority=1), SourceConfig(vendor="b", priority=2)])
    # FakeVendor "ok" trả 1 bản ghi; min_count=2 → a thiếu → thử b → b cũng 1 bản ghi → cả hai đều thiếu → lấy cái dài nhất (bằng nhau thì lấy a vì tới trước)
    r = e.fetch(Request(symbols=("600519",), market="CN"), min_count=2)
    assert r.ok and len(r.data) == 1  # Vẫn trả về (bản dài nhất), không vì thiếu mà báo lỗi


def test_min_count_returns_first_meeting_threshold():
    class MultiVendor:
        def __init__(self, name, n): self.name=name; self.supports_markets=set(); self._n=n
        def fetch(self, symbols, config): return [{"i": i} for i in range(self._n)]
    e = _engine({"a": MultiVendor("a", 1), "b": MultiVendor("b", 5)},
                [SourceConfig(vendor="a", priority=1), SourceConfig(vendor="b", priority=2)])
    r = e.fetch(Request(symbols=("x",), market="CN"), min_count=3)
    assert r.ok and r.vendor == "b" and len(r.data) == 5  # a thiếu (1<3) → b đủ (5≥3)


def test_min_count_default_one_unchanged():
    e = _engine({"a": FakeVendor("a", "ok")}, [SourceConfig(vendor="a", priority=1)])
    r = e.fetch(Request(symbols=("x",), market="CN"))  # Mặc định min_count=1
    assert r.ok and r.vendor == "a"


def test_engine_passes_request_limit_as_days_to_vendor():
    seen = {}
    class DaysVendor:
        name = "d"
        supports_markets: set = set()
        def fetch(self, symbols, config):
            seen["days"] = config.get("days")
            return [{"x": 1}]
    e = _engine({"d": DaysVendor()}, [SourceConfig(vendor="d", priority=1)])
    e.fetch(Request(symbols=("x",), market="CN", limit=250))
    assert seen["days"] == 250


def test_engine_passes_request_extra_to_vendor_config():
    # Test canh gác: các vendor như events cần req.extra (ví dụ since_days) được chuyển thẳng vào call_config.
    seen = {}
    class ExtraVendor:
        name = "e"
        supports_markets: set = set()
        def fetch(self, symbols, config):
            seen["since_days"] = config.get("since_days")
            seen["days"] = config.get("days")
            return [{"x": 1}]
    e = _engine({"e": ExtraVendor()}, [SourceConfig(vendor="e", priority=1)])
    e.fetch(Request(symbols=("x",), market="CN", limit=99, extra=(("since_days", 30),)))
    assert seen["since_days"] == 30
    assert seen["days"] == 99  # Việc chuyển tiếp extra không được phá vỡ phép nạp days sẵn có
