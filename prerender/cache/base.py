
class CacheBackend:
    async def get(self, key: str) -> bytes:
        raise NotImplementedError

    def set(self, key: str, payload: bytes, ttl: int = None, format: str = 'html') -> None:
        raise NotImplementedError
