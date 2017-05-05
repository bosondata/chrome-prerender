import os


CACHE_BACKEND = os.environ.get('CACHE_BACKEND', 'dummy')

if CACHE_BACKEND == 'disk':
    from .disk import DiskCache

    cache = DiskCache()
elif CACHE_BACKEND == 's3':
    from .s3 import S3Cache

    cache = S3Cache()
else:
    from .dummy import DummyCache

    cache = DummyCache()
