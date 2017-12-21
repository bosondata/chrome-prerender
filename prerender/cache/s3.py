import os
import io
import asyncio
from time import mktime
from urllib.parse import urlparse, quote_plus
from typing import Optional

import minio
import urllib3
import certifi

from .base import CacheBackend


S3_SERVER = os.environ.get('S3_SERVER', 's3.amazonaws.com')
S3_ACCESS_KEY = os.environ.get('S3_ACCESS_KEY')
S3_SECRET_KEY = os.environ.get('S3_SECRET_KEY')
S3_REGION = os.environ.get('S3_REGION')
S3_BUCKET = os.environ.get('S3_BUCKET', 'prerender')


class S3Cache(CacheBackend):
    def __init__(self) -> None:
        http_client = urllib3.PoolManager(
            timeout=urllib3.Timeout.DEFAULT_TIMEOUT,
            cert_reqs='CERT_REQUIRED',
            ca_certs=certifi.where(),
            retries=urllib3.Retry(
                total=5,
                backoff_factor=0.2,
                status_forcelist=[500, 502, 503, 504]
            ),
            maxsize=20
        )
        self.client = minio.Minio(
            S3_SERVER,
            access_key=S3_ACCESS_KEY,
            secret_key=S3_SECRET_KEY,
            region=S3_REGION,
            secure=S3_SERVER == 's3.amazonaws.com',
            http_client=http_client
        )

    async def get(self, key: str, format: str = 'html') -> Optional[bytes]:
        path = self._filename(key, format)
        loop = asyncio.get_event_loop()
        try:
            res = await loop.run_in_executor(None, self.client.get_object, S3_BUCKET, path)
        except (minio.error.NoSuchKey, asyncio.CancelledError):
            return
        return res.data

    def set(self, key: str, payload: bytes, ttl: int = None, format: str = 'html') -> None:
        path = self._filename(key, format)
        self.client.put_object(
            S3_BUCKET,
            path,
            io.BytesIO(payload),
            len(payload),
            metadata={'url': key, 'ttl': ttl}
        )

    async def modified_since(self, key: str, format: str = 'html') -> Optional[float]:
        path = self._filename(key, format)
        loop = asyncio.get_event_loop()
        try:
            res = await loop.run_in_executor(None, self.client.stat_object, S3_BUCKET, path)
        except (minio.error.NoSuchKey, asyncio.CancelledError):
            return
        return mktime(res.last_modified)

    def _filename(self, url, format):
        parsed_url = urlparse(url)
        encoded_name = quote_plus(parsed_url.path)
        if parsed_url.query:
            encoded_name += '?{}'.format(quote_plus(parsed_url.query))
        return os.path.join(parsed_url.hostname, encoded_name)
