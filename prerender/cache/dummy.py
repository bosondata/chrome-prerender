import time
from typing import Optional

from .base import CacheBackend


class DummyCache(CacheBackend):
    async def get(self, key: str, format: str = 'html') -> Optional[bytes]:
        return None

    def set(self, key: str, payload: bytes, ttl: int = None, format: str = 'html') -> None:
        pass

    async def modified_since(self, key: str, format: str = 'html') -> Optional[float]:
        return None
