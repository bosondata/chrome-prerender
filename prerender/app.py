import os
import sys
import time
import lzma
import logging
import logging.config
import asyncio
import warnings
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import cpu_count

import aiofiles
import aiofiles.os
from sanic import Sanic
from sanic import response
from sanic.exceptions import NotFound
from raven import Client
from raven_aiohttp import AioHttpTransport

from .prerender import Prerender, CONCURRENCY_PER_WORKER, TemporaryBrowserFailure


logger = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=max(cpu_count(), 5))

ALLOWED_DOMAINS = set(dm.strip() for dm in os.environ.get('PRERENDER_ALLOWED_DOMAINS', '').split(',') if dm.strip())
CACHE_ROOT_DIR = os.environ.get('CACHE_ROOT_DIR', '/tmp/prerender')
CACHE_LIVE_TIME = int(os.environ.get('CACHE_LIVE_TIME', 3600))
SENTRY_DSN = os.environ.get('SENTRY_DSN')
if SENTRY_DSN:
    sentry = Client(SENTRY_DSN, transport=AioHttpTransport)
else:
    sentry = None


def _get_cache_file_path(parsed_url):
    path = parsed_url.hostname
    path = os.path.join(path, os.path.normpath(parsed_url.path[1:]))
    if parsed_url.query:
        path = os.path.join(path, os.path.normpath(parsed_url.query))
    return os.path.join(CACHE_ROOT_DIR, path, 'prerender.cache.html')


async def _fetch_from_cache(path):
    loop = asyncio.get_event_loop()
    async with aiofiles.open(path, mode='rb') as f:
        res = await loop.run_in_executor(None, lzma.decompress, await f.read())
        return res.decode('utf-8')


def _save_to_cache(path, html):
    save_dir = os.path.dirname(path)
    try:
        os.makedirs(save_dir, 0o755)
    except OSError:
        pass
    try:
        compressed = lzma.compress(html.encode('utf-8'))
        with open(path, mode='wb') as f:
            f.write(compressed)
    except Exception:
        logger.exception('Error writing cache')


async def _is_cache_valid(path):
    if not os.path.exists(path):
        return False

    stat = await aiofiles.os.stat(path)
    if time.time() - stat.st_mtime <= CACHE_LIVE_TIME:
        return True
    return False


app = Sanic(__name__)


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


async def _render(prerender, url):
    '''Retry once after TemporaryBrowserFailure occurred.'''
    for i in range(2):
        try:
            return await prerender.render(url)
        except TemporaryBrowserFailure:
            if i < 1:
                logger.warning('Temporary browser failure, retry rendering %s', url)
                continue
            raise


@app.exception(NotFound)
async def handle_request(request, exception):
    # compatible with Sanic 0.4.1+
    url = getattr(request, 'path', request.url)
    if url.startswith('/http'):
        url = url[1:]
    if request.query_string:
        url = url + '?' + request.query_string
    parsed_url = urlparse(url)

    if not parsed_url.hostname:
        return response.text('Bad Request', status=400)

    if ALLOWED_DOMAINS:
        if parsed_url.hostname not in ALLOWED_DOMAINS:
            return response.text('Forbiden', status=403)

    cache_path = _get_cache_file_path(parsed_url)
    try:
        if await _is_cache_valid(cache_path):
            html = await _fetch_from_cache(cache_path)
            logger.info('Got 200 for %s in cache', url)
            return response.html(html, headers={'X-Prerender-Cache': 'hit'})
    except Exception:
        logger.exception('Error reading cache')

    if CONCURRENCY_PER_WORKER <= 0:
        # Read from cache only
        logger.warning('Got 502 for %s, prerender unavailable', url)
        return response.text('Bad Gateway', status=502)

    start_time = time.time()
    try:
        html = await _render(request.app.prerender, url)
        duration_ms = int((time.time() - start_time) * 1000)
        logger.info('Got 200 for %s in %dms', url, duration_ms)
        executor.submit(_save_to_cache, cache_path, html)
        return response.html(html, headers={'X-Prerender-Cache': 'miss'})
    except (asyncio.TimeoutError, asyncio.CancelledError):
        duration_ms = int((time.time() - start_time) * 1000)
        logger.warning('Got 504 for %s in %dms', url, duration_ms)
        return response.text('Gateway timeout', status=504)
    except Exception:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.exception('Internal Server Error for %s in %dms', url, duration_ms)
        if sentry:
            sentry.captureException()
        return response.text('Internal Server Error', status=500)


@app.listener('before_server_start')
async def before_server_start(app, loop):
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
async def after_server_stop(app, loop):
    if CONCURRENCY_PER_WORKER > 0:
        await app.prerender.shutdown()
