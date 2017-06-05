import os
import sys
import time
import logging
import logging.config
import asyncio
import warnings
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import cpu_count
from typing import Set

import raven
from sanic import Sanic
from sanic import response
from sanic.exceptions import NotFound
from raven_aiohttp import AioHttpTransport

from .prerender import Prerender, CONCURRENCY_PER_WORKER
from .cache import cache
from .exceptions import TemporaryBrowserFailure, TooManyResponseError


logger = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=cpu_count() * 5)

ALLOWED_DOMAINS: Set = set(dm.strip() for dm in
                           os.environ.get('PRERENDER_ALLOWED_DOMAINS', '').split(',') if dm.strip())
CACHE_LIVE_TIME: int = int(os.environ.get('CACHE_LIVE_TIME', 3600))
SENTRY_DSN = os.environ.get('SENTRY_DSN')

if SENTRY_DSN:
    sentry = raven.Client(
        SENTRY_DSN,
        transport=AioHttpTransport,
        release=raven.fetch_package_version('prerender'),
        site='Prerender',
    )
else:
    sentry = None


def _save_to_cache(key: str, data: bytes, format: str = 'html') -> None:
    try:
        cache.set(key, data, CACHE_LIVE_TIME, format)
    except Exception:
        logger.exception('Error writing cache')
        if sentry:
            sentry.captureException()


app = Sanic(__name__)
app.config.from_object(dict(
    KEEP_ALIVE=False,
))


@app.route('/browser/list')
async def list_browser_pages(request):
    renderer = request.app.prerender
    pages = await renderer.pages()
    return response.json(pages, ensure_ascii=False, indent=2, escape_forward_slashes=False)


@app.route('/browser/version')
async def show_brower_version(request):
    renderer = request.app.prerender
    version = await renderer.version()
    return response.json(version, ensure_ascii=False, indent=2, escape_forward_slashes=False)


@app.route('/browser/disable', methods=['PUT'])
async def disable_browser_rendering(request):
    global CONCURRENCY_PER_WORKER

    CONCURRENCY_PER_WORKER = 0
    return response.json({'message': 'success'})


@app.route('/browser/enable', methods=['PUT'])
async def enable_browser_rendering(request):
    global CONCURRENCY_PER_WORKER

    CONCURRENCY_PER_WORKER = int(os.environ['CONCURRENCY'])
    return response.json({'message': 'success'})


async def _render(prerender: Prerender, url: str, format: str = 'html') -> str:
    '''Retry once after TemporaryBrowserFailure occurred.'''
    for i in range(2):
        try:
            return await prerender.render(url, format)
        except (TemporaryBrowserFailure, asyncio.TimeoutError) as e:
            if i < 1:
                logger.warning('Temporary browser failure: %s, retry rendering %s in 1s', str(e), url)
                await asyncio.sleep(1)
                continue
            raise


@app.exception(NotFound)
async def handle_request(request, exception):
    start_time = time.time()
    format = 'html'
    url = request.path
    if url.startswith('/http'):
        url = url[1:]
    elif url.startswith('/html/http'):
        url = url[6:]
    elif url.startswith('/mhtml/http'):
        format = 'mhtml'
        url = url[7:]
    elif url.startswith('/pdf/http'):
        format = 'pdf'
        url = url[5:]
    elif url.startswith('/jpeg/http'):
        format = 'jpeg'
        url = url[6:]
    elif url.startswith('/png/http'):
        format = 'png'
        url = url[5:]
    if request.query_string:
        url = url + '?' + request.query_string
    parsed_url = urlparse(url)

    if not parsed_url.hostname:
        return response.text('Bad Request', status=400)

    if ALLOWED_DOMAINS:
        if parsed_url.hostname not in ALLOWED_DOMAINS:
            return response.text('Forbiden', status=403)

    skip_cache = request.method == 'POST'
    if not skip_cache:
        try:
            data = await cache.get(url, format)
            if data is not None:
                logger.info('Got 200 for %s in cache in %dms',
                            url,
                            int((time.time() - start_time) * 1000))
                if format == 'html':
                    return response.html(data.decode('utf-8'), headers={'X-Prerender-Cache': 'hit'})
                return response.raw(data, headers={'X-Prerender-Cache': 'hit'})
        except Exception:
            logger.exception('Error reading cache')
            if sentry:
                sentry.captureException()

    if CONCURRENCY_PER_WORKER <= 0:
        # Read from cache only
        logger.warning('Got 502 for %s in %dms, prerender unavailable',
                       url,
                       int((time.time() - start_time) * 1000))
        return response.text('Bad Gateway', status=502)

    try:
        data, status_code = await _render(request.app.prerender, url, format)
        logger.info('Got %d for %s in %dms',
                    status_code,
                    url,
                    int((time.time() - start_time) * 1000))
        if format == 'html':
            if 200 <= status_code < 300:
                executor.submit(_save_to_cache, url, data.encode('utf-8'), format)
            return response.html(data, headers={'X-Prerender-Cache': 'miss'}, status=status_code)
        if 200 <= status_code < 300:
            executor.submit(_save_to_cache, url, data, format)
        return response.raw(data, headers={'X-Prerender-Cache': 'miss'}, status=status_code)
    except (asyncio.TimeoutError, asyncio.CancelledError, TemporaryBrowserFailure):
        logger.warning('Got 504 for %s in %dms',
                       url,
                       int((time.time() - start_time) * 1000))
        return response.text('Gateway timeout', status=504)
    except TooManyResponseError:
        logger.warning('Too many response error for %s in %dms',
                       url,
                       int((time.time() - start_time) * 1000))
        return response.text('Service unavailable', status=503)
    except Exception:
        logger.exception('Internal Server Error for %s in %dms',
                         url,
                         int((time.time() - start_time) * 1000))
        if sentry:
            sentry.captureException()
        return response.text('Internal Server Error', status=500)


@app.listener('before_server_start')
async def before_server_start(app: Sanic, loop):
    loop.set_default_executor(executor)

    logging_config = {
        'version': 1,
        'disable_existing_loggers': False,
        'root': {
            'handlers': ['console'],
            'level': 'DEBUG' if app.debug else 'INFO'
        },
        'handlers': {
            'console': {
                'level': 'DEBUG' if app.debug else 'INFO',
                'class': 'logging.StreamHandler',
                'formatter': 'default',
                'stream': sys.stderr,
            }
        },
        'formatters': {
            'default': {
                'format': '%(asctime)s %(levelname)-2s %(name)s.%(funcName)s:%(lineno)-5d %(message)s',  # NOQA
            },
        },
    }
    logging.config.dictConfig(logging_config)
    if app.debug or loop.get_debug():
        warnings.simplefilter('always', ResourceWarning)

    app.prerender = Prerender(loop=loop)
    if CONCURRENCY_PER_WORKER > 0:
        try:
            await app.prerender.bootstrap()
        except Exception:
            logger.error('Error bootstrapping Prerender, please start Chrome first.')
            await app.prerender.shutdown()
            raise


@app.listener('after_server_stop')
async def after_server_stop(app: Sanic, loop):
    await app.prerender.shutdown()
