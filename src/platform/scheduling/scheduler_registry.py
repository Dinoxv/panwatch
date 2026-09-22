"""Sổ đăng ký nhẹ cho các bộ lập lịch đang chạy, để phần tự kiểm hệ thống đọc trạng thái sức khỏe.

Mỗi bộ lập lịch lúc start() thì register APScheduler của chính nó (có .running / .get_jobs()) vào đây;
probe_scheduler của phần tự kiểm dựa vào đó để xét "bộ lập lịch có đang chạy không".
Các tiến trình không có lịch chạy như CLI thì sổ đăng ký rỗng → bỏ qua êm.
"""

from __future__ import annotations

_REGISTRY: dict[str, object] = {}


def register(name: str, scheduler: object) -> None:
    _REGISTRY[name] = scheduler


def get_all() -> dict[str, object]:
    return dict(_REGISTRY)


def clear() -> None:
    _REGISTRY.clear()
