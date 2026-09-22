"""Adapter nguồn dữ liệu cho danh sách mã, đệm ở cấp dự án và tìm kiếm kiểu mờ.

Danh sách này API, lập lịch tác vụ và các module nghiệp vụ đều dùng chung, nên nó thuộc
nền tảng dữ liệu thị trường chứ không phải tầng HTTP. Đệm vẫn cố định nằm trong ``data/``
ở gốc dự án, tránh việc dời mã đi rồi âm thầm sinh thêm một bản đệm ``src/data``.
"""
import json
import os
import time
import logging
import concurrent.futures
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
CACHE_FILE = DATA_DIR / "stock_list_cache.json"
CACHE_TTL = 86400 * 7  # 7 days

# Cổ phiếu A trên EastMoney (dùng tên miền push2delay để tránh chuyển hướng)
EASTMONEY_URL = "http://80.push2delay.eastmoney.com/api/qt/clist/get"
EASTMONEY_PARAMS = {
    "po": "1",
    "np": "1",
    "fltt": "2",
    "invt": "2",
    "fid": "f12",
    "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
    "fields": "f12,f14",
}

# Tham số cổ phiếu Hồng Kông của EastMoney
EASTMONEY_HK_PARAMS = {
    "po": "1",
    "np": "1",
    "fltt": "2",
    "invt": "2",
    "fid": "f12",
    "fs": "m:128+t:3,m:128+t:4,m:128+t:1,m:128+t:2",  # Sàn chính, sàn GEM... của Hồng Kông
    "fields": "f12,f14",
}

# Tham số cổ phiếu Mỹ của EastMoney
EASTMONEY_US_PARAMS = {
    "po": "1",
    "np": "1",
    "fltt": "2",
    "invt": "2",
    "fid": "f12",
    "fs": "m:105,m:106,m:107",  # Cổ phiếu Mỹ: NYSE, NASDAQ, AMEX
    "fields": "f12,f14",
}

# Tham số Sở giao dịch Bắc Kinh của EastMoney (cổ phiếu A sàn Bắc Kinh)
EASTMONEY_BJ_PARAMS = {
    "po": "1",
    "np": "1",
    "fltt": "2",
    "invt": "2",
    "fid": "f12",
    "fs": "m:0+t:81",  # Sở giao dịch Bắc Kinh
    "fields": "f12,f14",
}
PAGE_SIZE = 100


def _load_cache() -> list[dict] | None:
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - data.get("ts", 0) < CACHE_TTL:
            return data["stocks"]
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def _save_cache(stocks: list[dict]):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"ts": time.time(), "stocks": stocks}, f, ensure_ascii=False)


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://quote.eastmoney.com/",
}


def _fetch_page(client: httpx.Client, page: int) -> list[dict]:
    """Lấy một trang trong danh sách cổ phiếu của Đông Tài"""
    params = {**EASTMONEY_PARAMS, "pn": str(page), "pz": str(PAGE_SIZE)}
    resp = client.get(EASTMONEY_URL, params=params, timeout=30, follow_redirects=True)
    data = resp.json()
    diff = data.get("data") or {}
    items = diff.get("diff") or []
    return [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "CN"} for item in items]


def _fetch_from_eastmoney() -> list[dict]:
    """Danh sách cổ phiếu A của Đông Tài (phân trang HTTP, lấy song song)"""
    with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=30) as client:
        # Trang đầu: lấy tổng số
        params = {**EASTMONEY_PARAMS, "pn": "1", "pz": str(PAGE_SIZE)}
        resp = client.get(EASTMONEY_URL, params=params)
        data = resp.json()
        root = data.get("data") or {}
        total = root.get("total", 0)
        first_items = root.get("diff") or []

        stocks = [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "CN"} for item in first_items]

        if total <= PAGE_SIZE:
            return stocks

        # Các trang còn lại lấy song song
        pages_needed = (total + PAGE_SIZE - 1) // PAGE_SIZE
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_page, client, pn): pn for pn in range(2, pages_needed + 1)}
            for future in concurrent.futures.as_completed(futures):
                try:
                    stocks.extend(future.result())
                except Exception as e:
                    logger.warning(f"东方财富第 {futures[future]} 页获取失败: {e}")

    return stocks


def _fetch_hk_page(client: httpx.Client, page: int) -> list[dict]:
    """Lấy một trang trong danh sách cổ phiếu HK của Đông Tài"""
    params = {**EASTMONEY_HK_PARAMS, "pn": str(page), "pz": str(PAGE_SIZE)}
    resp = client.get(EASTMONEY_URL, params=params, timeout=30, follow_redirects=True)
    data = resp.json()
    diff = data.get("data") or {}
    items = diff.get("diff") or []
    return [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "HK"} for item in items]


def _fetch_hk_from_eastmoney() -> list[dict]:
    """Danh sách cổ phiếu HK của Đông Tài"""
    with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=30) as client:
        params = {**EASTMONEY_HK_PARAMS, "pn": "1", "pz": str(PAGE_SIZE)}
        resp = client.get(EASTMONEY_URL, params=params)
        data = resp.json()
        root = data.get("data") or {}
        total = root.get("total", 0)
        first_items = root.get("diff") or []

        stocks = [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "HK"} for item in first_items]

        if total <= PAGE_SIZE:
            return stocks

        pages_needed = (total + PAGE_SIZE - 1) // PAGE_SIZE
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_hk_page, client, pn): pn for pn in range(2, pages_needed + 1)}
            for future in concurrent.futures.as_completed(futures):
                try:
                    stocks.extend(future.result())
                except Exception as e:
                    logger.warning(f"东方财富港股第 {futures[future]} 页获取失败: {e}")

    return stocks


def _fetch_bj_page(client: httpx.Client, page: int) -> list[dict]:
    """Lấy một trang trong danh sách sàn Bắc Kinh của Đông Tài"""
    params = {**EASTMONEY_BJ_PARAMS, "pn": str(page), "pz": str(PAGE_SIZE)}
    resp = client.get(EASTMONEY_URL, params=params, timeout=30, follow_redirects=True)
    data = resp.json()
    diff = data.get("data") or {}
    items = diff.get("diff") or []
    return [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "CN"} for item in items]


def _fetch_bj_from_eastmoney() -> list[dict]:
    """Danh sách sàn Bắc Kinh của Đông Tài (phân trang HTTP, lấy song song)"""
    with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=30) as client:
        # Trang đầu: lấy tổng số
        params = {**EASTMONEY_BJ_PARAMS, "pn": "1", "pz": str(PAGE_SIZE)}
        resp = client.get(EASTMONEY_URL, params=params)
        data = resp.json()
        root = data.get("data") or {}
        total = root.get("total", 0)
        first_items = root.get("diff") or []

        stocks = [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "CN"} for item in first_items]

        if total <= PAGE_SIZE:
            return stocks

        # Các trang còn lại lấy song song
        pages_needed = (total + PAGE_SIZE - 1) // PAGE_SIZE
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_bj_page, client, pn): pn for pn in range(2, pages_needed + 1)}
            for future in concurrent.futures.as_completed(futures):
                try:
                    stocks.extend(future.result())
                except Exception as e:
                    logger.warning(f"东方财富北交所第 {futures[future]} 页获取失败: {e}")

    return stocks


def _fetch_us_page(client: httpx.Client, page: int) -> list[dict]:
    """Lấy một trang trong danh sách cổ phiếu Mỹ của Đông Tài"""
    params = {**EASTMONEY_US_PARAMS, "pn": str(page), "pz": str(PAGE_SIZE)}
    resp = client.get(EASTMONEY_URL, params=params, timeout=30, follow_redirects=True)
    data = resp.json()
    diff = data.get("data") or {}
    items = diff.get("diff") or []
    return [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "US"} for item in items]


def _fetch_us_from_eastmoney() -> list[dict]:
    """Danh sách cổ phiếu Mỹ của Đông Tài"""
    with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=30) as client:
        params = {**EASTMONEY_US_PARAMS, "pn": "1", "pz": str(PAGE_SIZE)}
        resp = client.get(EASTMONEY_URL, params=params)
        data = resp.json()
        root = data.get("data") or {}
        total = root.get("total", 0)
        first_items = root.get("diff") or []

        stocks = [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "US"} for item in first_items]

        if total <= PAGE_SIZE:
            return stocks

        pages_needed = (total + PAGE_SIZE - 1) // PAGE_SIZE
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_us_page, client, pn): pn for pn in range(2, pages_needed + 1)}
            for future in concurrent.futures.as_completed(futures):
                try:
                    stocks.extend(future.result())
                except Exception as e:
                    logger.warning(f"东方财富美股第 {futures[future]} 页获取失败: {e}")

    return stocks


def _fetch_from_akshare() -> list[dict]:
    """Nguồn dữ liệu akshare (dự phòng, có thể gặp vấn đề SSL)"""
    import akshare as ak

    df = ak.stock_info_a_code_name()
    stocks = []
    for _, row in df.iterrows():
        stocks.append({
            "symbol": str(row["code"]),
            "name": str(row["name"]),
            "market": "CN",
        })
    return stocks


def refresh_stock_list() -> list[dict]:
    """Kéo danh sách cổ phiếu A và cổ phiếu HK rồi đệm lại"""
    stocks = []

    # Cổ phiếu A: ưu tiên EastMoney, akshare là dự phòng
    try:
        cn_stocks = _fetch_from_eastmoney()
        stocks.extend(cn_stocks)
        logger.info(f"东方财富获取 A 股列表成功: {len(cn_stocks)} 只")
    except Exception as e:
        logger.warning(f"东方财富获取 A 股失败: {e}")
        try:
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(_fetch_from_akshare)
                cn_stocks = future.result(timeout=15)
                stocks.extend(cn_stocks)
            logger.info(f"akshare 获取 A 股列表成功: {len(cn_stocks)} 只")
        except concurrent.futures.TimeoutError:
            logger.error("akshare 获取超时（15s）")
        except Exception as e2:
            logger.error(f"A 股数据源获取失败: {e2}")

    # Cổ phiếu Hồng Kông: EastMoney
    try:
        hk_stocks = _fetch_hk_from_eastmoney()
        stocks.extend(hk_stocks)
        logger.info(f"东方财富获取港股列表成功: {len(hk_stocks)} 只")
    except Exception as e:
        logger.warning(f"东方财富获取港股失败: {e}")

    # Cổ phiếu Mỹ: EastMoney
    try:
        us_stocks = _fetch_us_from_eastmoney()
        stocks.extend(us_stocks)
        logger.info(f"东方财富获取美股列表成功: {len(us_stocks)} 只")
    except Exception as e:
        logger.warning(f"东方财富获取美股失败: {e}")

    # Sở giao dịch Bắc Kinh: EastMoney
    try:
        bj_stocks = _fetch_bj_from_eastmoney()
        stocks.extend(bj_stocks)
        logger.info(f"东方财富获取北交所列表成功: {len(bj_stocks)} 只")
    except Exception as e:
        logger.warning(f"东方财富获取北交所失败: {e}")

    if stocks:
        _save_cache(stocks)
    return stocks


def get_stock_list() -> list[dict]:
    """获取股票列表(优先缓存)"""
    cached = _load_cache()
    if cached:
        return cached
    return refresh_stock_list()


def _realtime_search(query: str, market: str = "", limit: int = 20) -> list[dict]:
    """东方财富实时搜索 API"""
    import urllib.parse
    # Nâng count để phủ thêm ứng viên (gồm cả sàn Bắc Kinh)
    url = f"https://searchapi.eastmoney.com/api/suggest/get?input={urllib.parse.quote(query)}&type=14&count={limit * 5}"

    try:
        with httpx.Client(timeout=5) as client:
            resp = client.get(url, headers=HEADERS)
            data = resp.json()
    except Exception as e:
        logger.warning(f"实时搜索失败: {e}")
        return []

    items = data.get("QuotationCodeTable", {}).get("Data", [])
    if not items:
        return []

    def _normalize_symbol(code: str, mkt: str) -> str:
        c = (code or "").strip().upper()
        # Bỏ tiền tố / hậu tố thị trường nếu có, ví dụ SH000001 / SZ000001 / BJ830799 / 00700.HK / 836239.BJ
        for p in ("SH", "SZ", "BJ", "US", "HK"):
            if c.startswith(p):
                c = c[len(p):]
                break
        if "." in c:
            # Dạng như 00700.HK / 836239.BJ
            c = c.split(".")[0]
        if mkt == "HK":
            # Bảo đảm mã có 5 chữ số
            c = c.zfill(5)
        return c

    results = []
    for item in items:
        classify = (item.get("Classify") or "").strip()
        security_type = (item.get("SecurityTypeName") or "").strip()
        code_raw = (item.get("Code") or "").strip().upper()

        # Xác định thị trường
        if (
            classify in ("AStock", "BJStock")
            or any(ch in security_type for ch in ("沪", "深", "北"))
            or code_raw.endswith(".BJ")
            or code_raw.startswith("BJ")
        ):
            stock_market = "CN"
        elif classify == "HKStock" or "港" in security_type:
            stock_market = "HK"
        elif classify == "UsStock" or "美" in security_type:
            stock_market = "US"
        else:
            continue  # Bỏ qua các loại khác (trái phiếu, quỹ...)

        # Lọc theo thị trường
        if market and stock_market != market:
            continue

        # Chỉ giữ cổ phiếu (loại trái phiếu và các loại khác)
        type_us = item.get("TypeUS", "")
        if stock_market == "US" and type_us and type_us not in ("1", "2", "3"):  # 1 = cổ phiếu phổ thông, 3 = ADR/ADS...; 5 = ETF...
            continue

        code = item.get("Code", "")
        symbol = _normalize_symbol(code, stock_market)

        results.append({
            "symbol": symbol,
            "name": item.get("Name", ""),
            "market": stock_market,
        })

        if len(results) >= limit:
            break

    return results


def search_stocks(query: str, market: str = "", limit: int = 20) -> list[dict]:
    """搜索股票 - 优先使用实时搜索，失败则使用缓存"""
    q = query.strip()
    if not q:
        return []

    # Thử tìm theo thời gian thực
    results = _realtime_search(q, market, limit)
    if len(results) >= limit:
        return results[:limit]

    # Kết quả tìm thời gian thực chưa đủ thì bù bằng bộ đệm (tiện gộp kết quả tìm của nhiều thị trường)
    cached = _cached_search(q, market, limit)
    if not results:
        if cached:
            logger.info("实时搜索无结果，使用缓存搜索")
        return cached

    seen = {(r.get("market"), r.get("symbol")) for r in results}
    for r in cached:
        key = (r.get("market"), r.get("symbol"))
        if key in seen:
            continue
        results.append(r)
        seen.add(key)
        if len(results) >= limit:
            break
    return results


def _cached_search(query: str, market: str = "", limit: int = 20) -> list[dict]:
    """从缓存中模糊搜索股票"""
    stocks = get_stock_list()
    if not stocks:
        return []

    q = query.strip().upper()
    if not q:
        return []

    results = []
    for s in stocks:
        if market and s["market"] != market:
            continue
        code = s["symbol"].upper()
        name = s["name"].upper()
        # Ưu tiên khớp theo tiền tố mã
        if code.startswith(q):
            results.append((0, s))
        elif q in name:
            results.append((1, s))
        elif q in code:
            results.append((2, s))

        if len(results) >= limit * 2:
            break

    results.sort(key=lambda x: x[0])
    return [r[1] for r in results[:limit]]
