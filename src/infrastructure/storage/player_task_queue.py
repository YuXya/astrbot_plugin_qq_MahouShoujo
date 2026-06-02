from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator


class PlayerTaskQueue:
    def __init__(self):
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._group_locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def is_locked(self, group_id: str, user_id: str) -> bool:
        async with self._guard:
            lock = self._locks.get((group_id, user_id))
            return bool(lock and lock.locked())

    @asynccontextmanager
    async def lock_for(self, group_id: str, user_id: str) -> AsyncIterator[None]:
        key = (group_id, user_id)
        async with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock

        async with lock:
            yield

    @asynccontextmanager
    async def group_lock_for(self, group_id: str) -> AsyncIterator[None]:
        async with self._guard:
            lock = self._group_locks.get(group_id)
            if lock is None:
                lock = asyncio.Lock()
                self._group_locks[group_id] = lock

        async with lock:
            yield
