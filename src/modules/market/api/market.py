"""API chỉ số thị trường - dữ liệu công khai, không cần xác thực"""
import asyncio
import logging
import time
from fastapi import APIRouter

from src.platform.marketdata.collectors.kline_collector import get_index_klines
from src.platform.marketdata.models import MarketCode

logger = logging.getLogger(__name__)
router = APIRouter()


def get_market_data():
    """Import lười, tránh việc gói chưa cài / import vòng làm hỏng quá trình nạp module này."""
    from src.platform.marketdata.marketdata_client import get_market_data as _g

    return _g()

# Cấu hình các chỉ số thị trường chính
# response_symbol: symbol mà API Tencent trả về (dùng để khớp)
MARKET_INDICES = [
    # Chỉ số cổ phiếu A
    {"symbol": "000001", "name": "上证指数", "market": "CN", "tencent_symbol": "sh000001", "response_symbol": "000001"},
    {"symbol": "399001", "name": "深证成指", "market": "CN", "tencent_symbol": "sz399001", "response_symbol": "399001"},
    {"symbol": "399006", "name": "创业板指", "market": "CN", "tencent_symbol": "sz399006", "response_symbol": "399006"},
    # Chỉ số cổ phiếu Hồng Kông
    {"symbol": "HSI", "name": "恒生指数", "market": "HK", "tencent_symbol": "hkHSI", "response_symbol": "HSI"},
    # Chỉ số cổ phiếu Mỹ (symbol Tencent trả về có tiền tố dấu chấm: .IXIC, .DJI)
    {"symbol": "IXIC", "name": "纳斯达克", "market": "US", "tencent_symbol": "usIXIC", "response_symbol": ".IXIC"},
    {"symbol": "DJI", "name": "道琼斯", "market": "US", "tencent_symbol": "usDJI", "response_symbol": ".DJI"},
]

# Bộ đệm phản hồi chỉ số trong bộ nhớ: 60s (giá thị trường cần tươi).
_INDICES_CACHE: dict[str, tuple[float, list[dict]]] = {}
_INDICES_CACHE_TTL_S = 60

# Bộ đệm riêng cho spark (đóng cửa 20 phiên gần nhất): nến ngày một ngày mới đổi nên 30 phút là đủ tươi.
# Không có nó thì cứ 60s bộ đệm phản hồi hết hạn là phải trả giá thêm một vòng 6 lần lấy nến chỉ số (ở vài môi trường EastMoney hỏng trước rồi Tencent dự phòng,
# chạy nối tiếp mất khoảng 4s) — đây từng là nguồn trễ lớn nhất của làn nhanh ở trang chủ. Kết quả rỗng cũng đệm (nguồn hỏng thì đừng kéo lại liên tục).
_SPARK_CACHE: dict[str, tuple[float, list[float]]] = {}
_SPARK_TTL_S = 1800


def clear_indices_cache() -> None:
    """Xóa sạch đệm phản hồi/spark của chỉ số (dùng để cô lập khi test)."""
    _INDICES_CACHE.clear()
    _SPARK_CACHE.clear()


def _spark_for(idx: dict) -> list[float]:
    """Giá đóng cửa 20 ngày gần nhất, cho sparkline diễn biến chỉ số ở trang chủ (có đệm riêng 30 phút).

    fail-soft: mã thị trường sai/lấy dữ liệu lỗi/không có ánh xạ đều nuốt hết, trả danh sách rỗng, tuyệt đối không ảnh hưởng phần quote chính.
    """
    now = time.time()
    hit = _SPARK_CACHE.get(idx["symbol"])
    if hit and now - hit[0] < _SPARK_TTL_S:
        return hit[1]
    try:
        market_code = MarketCode(idx["market"])
        klines = get_index_klines(idx["symbol"], market_code, days=20)
        spark = [k.close for k in klines] if klines else []
    except Exception as e:
        logger.debug(f"指数 spark 获取失败 {idx['symbol']}: {e}")
        spark = []
    _SPARK_CACHE[idx["symbol"]] = (now, spark)
    return spark


@router.get("/indices")
async def get_market_indices():
    """Lấy các chỉ số thị trường chính (dữ liệu công khai, không cần xác thực)"""
    now = time.time()
    cached = _INDICES_CACHE.get("indices")
    if cached and now - cached[0] < _INDICES_CACHE_TTL_S:
        return cached[1]

    tencent_symbols = [idx["tencent_symbol"] for idx in MARKET_INDICES]

    try:
        quotes = get_market_data().index_quotes(tencent_symbols)
    except Exception as e:
        logger.error(f"获取市场指数失败: {e}")
        return []

    # Dựng ánh xạ response_symbol -> quote
    quote_map = {}
    for q in quotes:
        quote_map[q["symbol"]] = q

    # Lấy spark song song (bộ đệm còn hạn thì tốn 0; khởi động nguội = mất bằng cái chậm nhất ≈1s, thay vì cộng dồn 6 lần nối tiếp)
    sparks = await asyncio.gather(
        *[asyncio.to_thread(_spark_for, idx) for idx in MARKET_INDICES],
        return_exceptions=True,
    )
    spark_map = {
        idx["symbol"]: (sp if isinstance(sp, list) else [])
        for idx, sp in zip(MARKET_INDICES, sparks)
    }

    result = []
    for idx in MARKET_INDICES:
        # Khớp bằng response_symbol
        quote = quote_map.get(idx["response_symbol"])
        spark = spark_map.get(idx["symbol"], [])

        if quote:
            result.append({
                "symbol": idx["symbol"],
                "name": idx["name"],
                "market": idx["market"],
                "current_price": quote["current_price"],
                "change_pct": quote["change_pct"],
                "change_amount": quote["change_amount"],
                "prev_close": quote["prev_close"],
                "spark": spark,
            })
        else:
            # Không có dữ liệu giá thì vẫn trả về thông tin cơ bản
            result.append({
                "symbol": idx["symbol"],
                "name": idx["name"],
                "market": idx["market"],
                "current_price": None,
                "change_pct": None,
                "change_amount": None,
                "prev_close": None,
                "spark": spark,
            })

    _INDICES_CACHE["indices"] = (now, result)
    return result
