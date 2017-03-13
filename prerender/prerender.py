import os
import time
import lzma
import asyncio
import logging
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import cpu_count

import aiofiles
import aiofiles.os
from sanic import Sanic
from sanic import response
from sanic.exceptions import NotFound
from async_timeout import timeout

from .chromerdp import ChromeRemoteDebugger

logger = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=cpu_count())

PRERENDER_TIMEOUT = int(os.environ.get('PRERENDER_TIMEOUT', 30))
ALLOWED_DOMAINS = set(dm.strip() for dm in os.environ.get('PRERENDER_ALLOWED_DOMAINS', '').split(',') if dm.strip())
CACHE_ROOT_DIR = os.environ.get('CACHE_ROOT_DIR', '/tmp/prerender')
CACHE_LIVE_TIME = int(os.environ.get('CACHE_LIVE_TIME', 3600))
CONCURRENCY_PER_WORKER = int(os.environ.get('CONCURRENCY', cpu_count() * 2))


class Prerender:
    def __init__(self, host='localhost', port=9222, loop=None):
        self.host = host
        self.port = port
        self.loop = loop
        self._rdp = ChromeRemoteDebugger(host, port, loop=loop)
        self._ctrl_tab = None

    async def connect(self):
        tabs = await self._rdp.debuggable_tabs()
        self._ctrl_tab = tabs[0]
        await self._ctrl_tab.attach()
        logger.info('Connected to control tab %s', self._ctrl_tab.id)

    async def tabs(self):
        return await self._rdp.tabs()

    async def new_tab(self, url=None):
        await self._ctrl_tab.send({
            'method': 'Target.createTarget',
            'params': {
                'url': url or 'about:blank'
            }
        })
        res = await self._ctrl_tab.recv()
        tab_id = res['result']['targetId']
        logger.info('Created new tab %s', tab_id)
        tabs = await self._rdp.debuggable_tabs()
        tab = [tb for tb in tabs if tb.id == tab_id][0]
        return tab

    async def close_tab(self, tab_id):
        await self._ctrl_tab.send({
            'method': 'Target.closeTarget',
            'params': {'targetId': tab_id}
        })
        res = await self._ctrl_tab.recv()
        logger.info('Closed tab %s', tab_id)
        return res


async def prerender(renderer, url):
    tab = await renderer.new_tab()
    await tab.attach()
    try:
        await tab.listen()
        await tab.set_user_agent('Mozilla/5.0 (Linux) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/59.0.3033.0 Safari/537.36 Prerender (bosondata)')  # NOQA
        await tab.navigate(url)
        with timeout(PRERENDER_TIMEOUT):
            html = await tab.wait()
    finally:
        await tab.dettach()
        await renderer.close_tab(tab.id)
    return html


def _get_cache_file_path(parsed_url):
    path = parsed_url.hostname
    path = os.path.join(path, os.path.normpath(parsed_url.path[1:]))
    if parsed_url.query:
        path = os.path.join(path, os.path.normpath(parsed_url.query))
    return os.path.join(CACHE_ROOT_DIR, path, 'prerender.cache.html')


async def _fetch_from_cache(path, loop):
    async with aiofiles.open(path, mode='rb', executor=executor) as f:
        res = await loop.run_in_executor(executor, lzma.decompress, await f.read())
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

    stat = await aiofiles.os.stat(path, executor=executor)
    if time.time() - stat.st_mtime <= CACHE_LIVE_TIME:
        return True
    return False


app = Sanic(__name__)


@app.route('/browser/list')
async def list_browser_tabs(request):
    renderer = request.app.prerender
    tabs = await renderer.tabs()
    return response.json(tabs, ensure_ascii=False, indent=2, escape_forward_slashes=False)


@app.exception(NotFound)
async def handle_request(request, exception):
    url = request.url
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
            html = await _fetch_from_cache(cache_path, request.app.loop)
            logger.info('Got 200 for %s in cache', url)
            return response.html(html, headers={'X-Prerender-Cache': 'hit'})
    except Exception:
        logger.exception('Error reading cache')

    start_time = time.time()
    try:
        async with request.app.semaphore:
            html = await prerender(request.app.prerender, url)
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
        return response.text('Internal Server Error', status=500)


@app.listener('after_server_start')
async def after_server_start(app, loop):
    app.prerender = Prerender(loop=loop)
    app.semaphore = asyncio.Semaphore(CONCURRENCY_PER_WORKER, loop=loop)
    await app.prerender.connect()
