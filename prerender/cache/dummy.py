
from .base import CacheBackend


class DummyCache(CacheBackend):
    async def get(self, key: str, format: str = 'html') -> bytes:
        return None

    def set(self, key: str, payload: bytes, ttl: int = None, format: str = 'html') -> None:
        pass
