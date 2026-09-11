"""Small session caches, bounded by PNG payload bytes and entry count."""

from collections import OrderedDict
from collections.abc import Hashable
from typing import Generic, TypeVar

T = TypeVar("T")


class ImageCache(Generic[T]):
    def __init__(self, max_bytes: int = 16 * 1024 * 1024, max_entries: int = 256) -> None:
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self._entries: OrderedDict[Hashable, tuple[T, int]] = OrderedDict()
        self._bytes = 0

    def get(self, key: Hashable) -> T | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        self._entries.move_to_end(key)
        return entry[0]

    def put(self, key: Hashable, value: T, size: int) -> None:
        previous = self._entries.pop(key, None)
        if previous is not None:
            self._bytes -= previous[1]
        if size > self.max_bytes or self.max_entries <= 0:
            return
        while self._entries and (
            self._bytes + size > self.max_bytes or len(self._entries) >= self.max_entries
        ):
            _, (_, removed_size) = self._entries.popitem(last=False)
            self._bytes -= removed_size
        self._entries[key] = value, size
        self._bytes += size
