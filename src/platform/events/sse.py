"""Hạ tầng SSE (Server-Sent Events).

Cung cấp hai năng lực:
1. `format_sse_event`: mã hóa sự kiện thành định dạng SSE wire (kèm id tự tăng, cho Last-Event-ID đẩy tiếp).
2. `SSEStream` / `SSEHub`: vùng đệm sự kiện tách rời quá trình sinh nội dung khỏi kết nối.
   - Bên sản xuất (tác vụ nền) publish sự kiện vào `SSEStream`, không liên quan tới kết nối HTTP, đứt kết nối không làm gián đoạn việc sinh;
   - Bên tiêu thụ (điểm cuối SSE) subscribe từ số thứ tự bất kỳ, đứt rồi nối lại mang Last-Event-ID là đẩy tiếp được;
   - Luồng kết thúc (finish) rồi vẫn giữ lại một khoảng (TTL), cho các lần nối lại muộn đọc trọn sự kiện.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field

# Thời gian giữ lại sau khi luồng kết thúc (giây): đủ để giao diện kết nối lại và lấy trọn kết quả
STREAM_TTL_SEC = 600
# Trần số sự kiện của một luồng (chặn phòng thủ, tránh tác vụ lỗi làm tràn bộ nhớ)
MAX_EVENTS_PER_STREAM = 10000


def format_sse_event(seq: int, event: str, data: dict | str) -> str:
    """Mã hóa một sự kiện SSE (id + event + data, data thống nhất là JSON)."""
    if not isinstance(data, str):
        data = json.dumps(data, ensure_ascii=False)
    # data có xuống dòng thì tách thành nhiều dòng data: theo giao thức SSE
    data_lines = "".join(f"data: {line}\n" for line in data.split("\n"))
    return f"id: {seq}\nevent: {event}\n{data_lines}\n"


def format_sse_comment(text: str = "keepalive") -> str:
    """Mã hóa dòng chú thích SSE (nhịp tim, chống proxy ngắt kết nối rảnh)."""
    return f": {text}\n\n"


@dataclass
class _Event:
    seq: int
    event: str
    data: dict | str


@dataclass
class SSEStream:
    """Một luồng sự kiện phát lại được (bên sản xuất và bên tiêu thụ tách rời)."""

    stream_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.monotonic)
    done: bool = False

    def __post_init__(self):
        self._events: list[_Event] = []
        self._cond = asyncio.Condition()

    async def publish(self, event: str, data: dict | str) -> int:
        """Thêm một sự kiện, trả về số thứ tự của nó (bắt đầu từ 1)."""
        async with self._cond:
            if len(self._events) >= MAX_EVENTS_PER_STREAM:
                # Vượt trần thì đặt luôn trạng thái kết thúc, tránh phình vô hạn
                self.done = True
                self._cond.notify_all()
                return len(self._events)
            seq = len(self._events) + 1
            self._events.append(_Event(seq=seq, event=event, data=data))
            self._cond.notify_all()
            return seq

    async def finish(self) -> None:
        """Đánh dấu luồng kết thúc (bên đăng ký đọc hết vùng đệm rồi tự thoát)."""
        async with self._cond:
            self.done = True
            self._cond.notify_all()

    async def subscribe(self, after_seq: int = 0, heartbeat_sec: float = 15.0):
        """Tiêu thụ sự kiện từ sau after_seq (generator bất đồng bộ, sinh ra chuỗi định dạng SSE wire).

        - Phát lại các sự kiện đã có trong vùng đệm trước (mấu chốt để đứt rồi nối lại đẩy tiếp theo Last-Event-ID);
        - Đuổi kịp rồi thì chặn chờ sự kiện mới; chờ quá heartbeat_sec thì sinh chú thích nhịp tim;
        - Luồng đã done và đọc hết vùng đệm thì kết thúc.
        """
        cursor = max(0, int(after_seq))
        while True:
            batch: list[_Event] = []
            async with self._cond:
                if cursor < len(self._events):
                    batch = self._events[cursor:]
                    cursor = len(self._events)
                elif self.done:
                    return
                else:
                    try:
                        await asyncio.wait_for(self._cond.wait(), timeout=heartbeat_sec)
                    except asyncio.TimeoutError:
                        pass
            if batch:
                for ev in batch:
                    yield format_sse_event(ev.seq, ev.event, ev.data)
            else:
                async with self._cond:
                    idle = not (cursor < len(self._events) or self.done)
                if idle:
                    yield format_sse_comment()


class SSEHub:
    """Quản lý nhiều SSEStream theo stream_id, có dọn theo TTL."""

    def __init__(self, ttl_sec: float = STREAM_TTL_SEC):
        self._streams: dict[str, SSEStream] = {}
        self._ttl_sec = ttl_sec

    def create(self) -> SSEStream:
        self._prune()
        stream = SSEStream()
        self._streams[stream.stream_id] = stream
        return stream

    def get(self, stream_id: str) -> SSEStream | None:
        self._prune()
        return self._streams.get(stream_id)

    def _prune(self) -> None:
        """Dọn các luồng cũ quá TTL."""
        now = time.monotonic()
        expired = [
            sid for sid, s in self._streams.items()
            if now - s.created_at > self._ttl_sec
        ]
        for sid in expired:
            self._streams.pop(sid, None)


# Hub toàn cục cho luồng hội thoại chat (thực thể duy nhất trong tiến trình; tác vụ sinh nội dung và kết nối SSE tách rời qua nó)
chat_stream_hub = SSEHub()
