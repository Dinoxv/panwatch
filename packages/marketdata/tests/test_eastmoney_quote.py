import marketdata.vendors.eastmoney as ev
from marketdata.symbol import Symbol
from marketdata.types import Quote


def _fake_data(code: str = "600519", name: str = "贵州茅台") -> dict:
    """构造真实形态的 push2 stock/get JSON(f59=2 位小数,价格字段为放大 100 倍的整数)。

    current=1700.00 prev_close=1680.00 open=1685.00 high=1710.00 low=1670.00
    change_amount=20.00(=current-prev) change_pct=1.19%(≈20/1680)
    """
    return {
        "f43": 170000,     # Giá mới nhất (thô) → /10^2 = 1700,00
        "f44": 171000,     # Cao nhất → 1710,00
        "f45": 167000,     # Thấp nhất → 1670,00
        "f46": 168500,     # Mở cửa → 1685,00
        "f47": 12345,      # Khối lượng (lô)
        "f48": 6789000000, # Giá trị giao dịch (đồng)
        "f50": 120,        # Tỷ lệ khối lượng (thô) → /100 = 1,20
        "f55": 999,        # Không dùng làm trường chính trong vendor này (tỷ lệ vòng quay của CN đi qua f168)
        "f57": code,       # Mã
        "f58": name,       # Tên
        "f59": 2,          # Số chữ số thập phân
        "f60": 168000,     # Đóng cửa phiên trước → 1680,00
        "f116": 2100050000000,  # Vốn hóa (giá trị thô, đồng) → /1e8 = 21000,5 (trăm triệu)
        "f117": 2100050000000,  # Vốn hóa lưu hành (giá trị thô, đồng) → /1e8 = 21000,5 (trăm triệu)
        "f168": 50,        # Tỷ lệ vòng quay (thô) → /100 = 0,50%
        "f169": 2000,      # Mức tăng giảm (thô) → /10^2 = 20,00
        "f170": 119,       # Biên độ (thô) → /100 = 1,19%
        "f171": 500,       # Biên dao động (chưa ánh xạ vào Quote, bỏ qua)
    }


def _payload(code: str = "600519", name: str = "贵州茅台") -> dict:
    return {"data": _fake_data(code, name)}


def test_eastmoney_parses_quote_with_decimal_restore(monkeypatch):
    monkeypatch.setattr(ev, "market_get", lambda *a, **k: _payload())
    v = ev.EastmoneyQuoteVendor()
    out = v.fetch([Symbol.parse("600519", market="CN")], {})
    assert len(out) == 1
    q = out[0]
    assert isinstance(q, Quote)
    assert q.symbol == "600519" and q.name == "贵州茅台" and q.market == "CN"
    assert q.current_price == 1700.0
    assert q.prev_close == 1680.0
    assert q.open_price == 1685.0
    assert q.high_price == 1710.0
    assert q.low_price == 1670.0
    assert q.change_amount == 20.0
    assert q.change_pct == 1.19
    assert q.turnover_rate == 0.5
    assert q.volume_ratio == 1.2
    assert q.volume == 12345.0
    assert q.turnover == 6789000000.0
    assert q.total_market_value == 21000.5
    assert q.circulating_market_value == 21000.5


def test_eastmoney_batch_multiple_symbols_loops_calls(monkeypatch):
    calls: list[dict] = []

    def fake_market_get(url, *, params=None, **kwargs):
        calls.append(params or {})
        secid = (params or {}).get("secid", "")
        if secid.endswith("600519"):
            return _payload("600519", "贵州茅台")
        if secid.endswith("000001"):
            return _payload("000001", "平安银行")
        return None

    monkeypatch.setattr(ev, "market_get", fake_market_get)
    v = ev.EastmoneyQuoteVendor()
    symbols = [Symbol.parse("600519", market="CN"), Symbol.parse("000001", market="CN")]
    out = v.fetch(symbols, {})
    assert len(calls) == 2  # Truy vấn từng mã, lặp lần lượt
    assert len(out) == 2
    codes = {q.symbol for q in out}
    assert codes == {"600519", "000001"}


def test_eastmoney_empty_response_returns_empty(monkeypatch):
    monkeypatch.setattr(ev, "market_get", lambda *a, **k: None)
    out = ev.EastmoneyQuoteVendor().fetch([Symbol.parse("600519", market="CN")], {})
    assert out == []


def test_eastmoney_no_symbols_returns_empty():
    assert ev.EastmoneyQuoteVendor().fetch([], {}) == []


def test_eastmoney_unsupported_market_skipped(monkeypatch):
    # Vendor này chỉ phục vụ CN; symbol HK/US phải bị bỏ qua, không gửi request
    calls = {"n": 0}

    def fake_market_get(*a, **k):
        calls["n"] += 1
        return _payload()

    monkeypatch.setattr(ev, "market_get", fake_market_get)
    out = ev.EastmoneyQuoteVendor().fetch([Symbol.parse("00700", market="HK")], {})
    assert out == []
    assert calls["n"] == 0


def test_eastmoney_missing_data_key_returns_empty(monkeypatch):
    monkeypatch.setattr(ev, "market_get", lambda *a, **k: {"data": None})
    out = ev.EastmoneyQuoteVendor().fetch([Symbol.parse("600519", market="CN")], {})
    assert out == []
