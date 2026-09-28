from __future__ import annotations

import time
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class TtlCache(Generic[T]):
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, T]] = {}

    def get(self, key: str) -> T | None:
        item = self._store.get(key)
        if item is None:
            return None
        expires_at, value = item
        if time.monotonic() >= expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: T, ttl_seconds: float) -> T:
        self._store[key] = (time.monotonic() + ttl_seconds, value)
        return value

    def clear(self) -> None:
        self._store.clear()


cache: TtlCache[Any] = TtlCache()
