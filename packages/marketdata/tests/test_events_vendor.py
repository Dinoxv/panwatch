from datetime import date, timedelta

import marketdata.vendors.events as ev
from marketdata.symbol import Symbol
from marketdata.types import EventItem


def test_events_parses_and_filters(monkeypatch):
    # Cấu trúc và tên trường của phản hồi ann (EastMoney) lấy theo đúng những gì _parse_item trong src/collectors/events_collector.py thực sự đọc:
    # data.list[].{art_code, title, notice_date, columns[].column_name, codes[].stock_code}
    #
    # Ngày sinh ra tương đối so với «hôm nay»: cửa sổ since_days là cửa sổ trượt, nên test gán cứng ngày sẽ đột ngột bị lọc sạch
    # và fail vào đúng ngày vượt khỏi cửa sổ (đã từng xảy ra).
    recent = date.today() - timedelta(days=2)
    older = date.today() - timedelta(days=4)
    recent_code = f"AN{recent:%Y%m%d}0002"
    older_code = f"AN{older:%Y%m%d}0001"

    payload = {
        "success": True,
        "data": {
            "list": [
                # Mới hơn, "mua lại cổ phiếu" -> event_type=repurchase, importance=2
                {
                    "art_code": recent_code,
                    "title": "贵州茅台股份有限公司关于回购股份的公告",
                    "notice_date": f"{recent:%Y-%m-%d} 10:00:00",
                    "columns": [{"column_name": "临时公告"}],
                    "codes": [{"stock_code": "600519"}],
                },
                # Cũ hơn, "tái cơ cấu tài sản trọng yếu" -> event_type=restructuring, importance=3
                {
                    "art_code": older_code,
                    "title": "贵州茅台股份有限公司关于重大资产重组的公告",
                    "notice_date": f"{older:%Y-%m-%d} 09:00:00",
                    "columns": [{"column_name": "重大事项"}],
                    "codes": [{"stock_code": "600519"}],
                },
                # art_code trùng với bản ghi đầu -> phải bị khử trùng lặp
                {
                    "art_code": recent_code,
                    "title": "贵州茅台股份有限公司关于回购股份的公告(重复)",
                    "notice_date": f"{recent:%Y-%m-%d} 10:00:00",
                    "columns": [{"column_name": "临时公告"}],
                    "codes": [{"stock_code": "600519"}],
                },
            ]
        },
    }
    monkeypatch.setattr(ev, "market_get", lambda *a, **k: payload)

    # Trộn vào một mã không thuộc cổ phiếu A (mã Hồng Kông 5 chữ số) để kiểm chứng phép lọc cổ phiếu A chỉ tác động lên symbols, không đổi cấu trúc trả về.
    symbols = [Symbol.parse("600519"), Symbol.parse("00700")]
    out = ev.EventsVendor().fetch(symbols, {"since_days": 30})

    assert all(isinstance(x, EventItem) for x in out)
    # Khử trùng lặp có hiệu lực: 3 bản ghi đầu vào -> 2 bản ghi duy nhất theo (source, external_id)
    assert len(out) == 2

    # Sắp xếp: theo (publish_time, importance) giảm dần -> công bố mua lại mới hơn đứng đầu
    assert out[0].external_id == recent_code
    assert out[0].event_type == "repurchase"
    assert out[0].importance == 2
    assert out[0].symbols == ["600519"]
    assert out[0].source == "eastmoney"
    assert (
        out[0].url
        == f"https://data.eastmoney.com/notices/detail/600519/{recent_code}.html"
    )

    assert out[1].external_id == older_code
    assert out[1].event_type == "restructuring"
    assert out[1].importance == 3


def test_events_empty(monkeypatch):
    monkeypatch.setattr(ev, "market_get", lambda *a, **k: {"success": True, "data": {"list": []}})
    assert ev.EventsVendor().fetch([Symbol.parse("600519")], {}) == []
