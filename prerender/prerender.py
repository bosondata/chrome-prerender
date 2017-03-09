import os
import asyncio
import logging
from urllib.parse import urlparse

from sanic import Sanic
from sanic import response
from sanic.exceptions import NotFound
from async_timeout import timeout

from .chromerdp import ChromeRemoteDebugger

logger = logging.getLogger(__name__)
PRERENDER_TIMEOUT = int(os.environ.get('PRERENDER_TIMEOUT', 30))
ALLOWED_DOMAINS = set(dm.strip() for dm in os.environ.get('PRERENDER_ALLOWED_DOMAINS', '').split(',') if dm.strip())


class Prerender:
    def __init__(self, host='localhost', port=9222, loop=None):
        self.host = host
        self.port = port
        self.loop = loop
        self._rdp = ChromeRemoteDebugger(host, port, loop=loop)
        self._ctrl_tab = None

    async def connect(self):
        tabs = await self._rdp.tabs()
        self._ctrl_tab = tabs[0]
        await self._ctrl_tab.attach()
        logger.info('Connected to control tab %s', self._ctrl_tab.id)

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
        tabs = await self._rdp.tabs()
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

    async def close(self):
        tabs = await self._rdp.tabs()
        for tab in tabs:
            await tab.close()
        logger.info('All tabs closed')


async def prerender(renderer, url):
    tab = await renderer.new_tab()
    await tab.attach()
    await tab.listen()
    await tab.set_user_agent('Mozilla/5.0 (Linux) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/59.0.3033.0 Safari/537.36 Prerender (bosondata)')  # NOQA
    try:
        await tab.navigate(url)
        with timeout(PRERENDER_TIMEOUT):
            html = await tab.wait()
    finally:
        await renderer.close_tab(tab.id)
    return html


app = Sanic(__name__)


@app.exception(NotFound)
async def handle_request(request, exception):
    url = request.url
    if url.startswith('/http'):
        url = url[1:]

    if ALLOWED_DOMAINS:
        parsed_url = urlparse(url)
        if parsed_url.hostname not in ALLOWED_DOMAINS:
            return response.text('Forbiden', status=403)
    try:
        html = await prerender(request.app.prerender, url)
        logger.info('Got 200 for %s', url)
        return response.text(html)
    except asyncio.TimeoutError:
        logger.warning('Got 504 for %s', url)
        return response.text('Gateway timeout', status=504)
    except Exception:
        logger.exception('Internal Server Error for %s', url)
        return response.text('Internal Server Error', status=500)


@app.listener('after_server_start')
def after_server_start(app, loop):
    app.prerender = Prerender(loop=loop)
    loop.run_until_complete(app.prerender.connect())
