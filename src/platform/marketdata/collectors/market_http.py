"""Tiện ích HTTP thống nhất cho việc lấy bảng giá/thu thập dữ liệu.

Gom phần khuôn mẫu vốn rải rác ở các collector về một chỗ, tránh mỗi tệp tự viết một bản
mà bản nào cũng thiếu chỗ này chỗ kia:
- **Đi qua proxy hệ thống**: mặc định trust_env=True, tuân theo HTTP_PROXY/NO_PROXY trong env của tiến trình (do apply_proxy_env đặt theo thiết lập http_proxy trên giao diện). Không cấu hình proxy thì nối thẳng.
- **Tiết lưu theo host**: khoảng cách tối thiểu giữa các yêu cầu tới cùng tên miền, làm mượt các đợt dồn tuần tự/song song (bên thứ ba gặp dồn hàng loạt sẽ giới hạn tần suất).
- **Thử lại có lùi**: phản hồi rỗng/lỗi thì lùi + thêm nhiễu rồi thử lại.
- **Đánh dấu nơi gọi**: cả dự án dùng chung một contextvar, nhật ký lỗi kèm [src=xxx], để lần ra tác vụ nào kích hoạt.

Dấu nơi gọi dùng chung toàn cục: lối vào lập lịch nào bọc `with fetch_source("xxx"):` thì
mọi nhật ký lỗi của mọi collector (nến/báo giá/dòng tiền/...) trong tác vụ đó đều mang
cùng một nơi gọi. asyncio.to_thread có truyền contextvars, nên đặt trong phần lập lịch bất
đồng bộ cũng lọt được sang luồng worker.
"""

from __future__ import annotations

import contextvars
import logging
import random
import threading
import time
from contextlib import ExitStack, contextmanager
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ── Dấu nguồn gọi (dùng chung toàn cục) ──────────────────────────────────
_FETCH_SOURCE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "fetch_source", default=""
)


@contextmanager
def fetch_source(name: str):
    """Đánh dấu nơi gọi của lần lấy dữ liệu này, ghi vào nhật ký lỗi để dễ lần ra bên kích hoạt.

    Đồng bộ chuyển thẳng sang contextvar HTTP của chính gói marketdata; nếu không thì dù
    bộ lập lịch của host đã đánh dấu ``outcome_eval``, nhật ký Tencent/Stooq bên trong gói
    vẫn hiện nơi gọi rỗng.
    """
    token = _FETCH_SOURCE.set(name or "")
    stack = ExitStack()
    try:
        try:
            from marketdata.http import fetch_source as package_fetch_source

            stack.enter_context(package_fetch_source(name))
        except Exception:
            # marketdata là phụ thuộc tùy chọn; bộ thu thập của bên chủ quản vẫn phải chạy độc lập được.
            pass
        yield
    finally:
        stack.close()
        _FETCH_SOURCE.reset(token)


def source_suffix() -> str:
    src = _FETCH_SOURCE.get()
    return f" [src={src}]" if src else ""


# ── Giãn nhịp theo host ở cấp tiến trình ─────────────────────────────────
_THROTTLE_LOCK = threading.Lock()
_last_call: dict[str, float] = {}


def throttle(host_key: str, min_interval_s: float) -> None:
    """Đảm bảo khoảng cách giữa các yêu cầu tới cùng một host ≥ min_interval_s, làm mượt các đợt dồn tuần tự/song song."""
    if min_interval_s <= 0:
        return
    with _THROTTLE_LOCK:
        wait = min_interval_s - (time.time() - _last_call.get(host_key, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_call[host_key] = time.time()


# ── Hàm GET đồng bộ dùng chung ───────────────────────────────────────────
def market_get(
    url: str,
    *,
    host_key: str,
    params: dict | None = None,
    headers: dict | None = None,
    min_interval_s: float = 0.0,
    timeout: float = 10.0,
    retries: int = 2,
    backoff: float = 0.4,
    jitter: float = 0.25,
    parse: str = "text",  # "text" | "json" | "content"
    encoding: str | None = None,  # Ép bảng mã khi giải (ví dụ "gbk")
    symbol: str = "",
    log_label: str = "",
    raise_for_status: bool = True,
    trust_env: bool = True,  # Tuân theo proxy trong env của tiến trình (HTTP_PROXY/NO_PROXY), do apply_proxy_env đặt thống nhất
    follow_redirects: bool = True,
    verify: bool = True,
) -> Any | None:
    """Đi theo proxy hệ thống (env) + tiết lưu theo host + thử lại có lùi. Thành công trả kết quả đã đọc, hỏng trả None và ghi nhật ký kèm nơi gọi."""
    last_err: Any = None
    for attempt in range(max(1, retries + 1)):
        throttle(host_key, min_interval_s)
        try:
            with httpx.Client(
                follow_redirects=follow_redirects,
                timeout=timeout + attempt * 4,
                headers=headers,
                trust_env=trust_env,
                verify=verify,
            ) as client:
                resp = client.get(url, params=params)
                if raise_for_status:
                    resp.raise_for_status()
                if parse == "json":
                    return resp.json()
                if parse == "content":
                    return resp.content
                if encoding:
                    return resp.content.decode(encoding, errors="ignore")
                return resp.text
        except Exception as e:
            last_err = e
        if attempt < retries:
            time.sleep(backoff * (attempt + 1) + random.uniform(0, jitter))

    if last_err is not None:
        label = log_label or host_key
        sym = f" symbol={symbol}" if symbol else ""
        logger.warning(f"{label} 获取失败{sym}: {last_err}{source_suffix()}")
    return None


# ── Bộ đệm TTL nhẹ ───────────────────────────────────────────────────────
# Tương đương src/core/providers/cache.py, nhưng đặt ở module tầng đáy của lớp thu thập để các collector
# dùng lại trực tiếp — tránh việc collector import ngược gói providers gây phụ thuộc vòng.
class TTLCache:
    """Đệm TTL trong bộ nhớ một tiến trình, an toàn với luồng, khóa hết hạn bị loại thụ động ở lần get kế tiếp."""

    def __init__(self, default_ttl_sec: float = 20.0, max_size: int = 2048):
        self._default_ttl = default_ttl_sec
        self._max_size = max_size
        self._lock = threading.Lock()
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            value, expires_at = entry
            if expires_at <= now:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl_sec: float | None = None) -> None:
        ttl = ttl_sec if ttl_sec is not None else self._default_ttl
        if ttl <= 0:
            return  # Tường minh không đệm
        expires = time.monotonic() + ttl
        with self._lock:
            if len(self._store) >= self._max_size and key not in self._store:
                oldest = min(self._store.items(), key=lambda kv: kv[1][1])
                del self._store[oldest[0]]
            self._store[key] = (value, expires)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
