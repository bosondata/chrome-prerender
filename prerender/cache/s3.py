import os
import io
import codecs
import asyncio
from urllib.parse import urlparse
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
        self.client = minio.Minio(
            S3_SERVER,
            access_key=S3_ACCESS_KEY,
            secret_key=S3_SECRET_KEY,
            region=S3_REGION,
            secure=S3_SERVER == 's3.amazonaws.com',
        )
        self.client._http = urllib3.PoolManager(
            timeout=self.client._conn_timeout,
            cert_reqs='CERT_REQUIRED',
            ca_certs=certifi.where(),
            retries=urllib3.Retry(
                total=5,
                backoff_factor=0.2,
                status_forcelist=[500, 502, 503, 504]
            ),
            maxsize=20
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

    def _filename(self, url, format):
        hex_name = codecs.encode(url.encode('utf-8'), 'hex').decode('utf-8')
        sub_dir = os.path.join(hex_name[:2], hex_name[2:4])
        name = hex_name[4:] + '.{}'.format(format)
        parsed_url = urlparse(url)
        return os.path.join(parsed_url.hostname, sub_dir, name)
