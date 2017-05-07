import os
import lzma
import asyncio
from typing import Optional

import diskcache

from .base import CacheBackend


CACHE_ROOT_DIR: str = os.environ.get('CACHE_ROOT_DIR', '/tmp/prerender')


class DiskCache(CacheBackend):
    def __init__(self) -> None:
        self._cache = diskcache.Cache(CACHE_ROOT_DIR)

    async def get(self, key: str, format: str = 'html') -> Optional[bytes]:
        loop = asyncio.get_event_loop()
        cache_get = self._cache.get
        data = await loop.run_in_executor(None, cache_get, key + format)
        if data is not None:
            res = await loop.run_in_executor(None, lzma.decompress, data)
            return res

    def set(self, key: str, payload: bytes, ttl: int = None, format: str = 'html') -> None:
        compressed = lzma.compress(payload)
        self._cache.set(key + format, compressed, expire=ttl)
