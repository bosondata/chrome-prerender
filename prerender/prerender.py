import os
import asyncio
import logging
from multiprocessing import cpu_count

from async_timeout import timeout

from .chromerdp import ChromeRemoteDebugger

logger = logging.getLogger(__name__)

PRERENDER_TIMEOUT = int(os.environ.get('PRERENDER_TIMEOUT', 30))
CONCURRENCY_PER_WORKER = int(os.environ.get('CONCURRENCY', cpu_count() * 2))


class Prerender:
    def __init__(self, host='localhost', port=9222, loop=None):
        self.host = host
        self.port = port
        self.loop = loop
        self._rdp = ChromeRemoteDebugger(host, port, loop=loop)
        self._ctrl_tab = None
        self._idle_tabs = asyncio.Queue(CONCURRENCY_PER_WORKER, loop=self.loop)

    async def connect(self):
        tabs = await self._rdp.debuggable_tabs()
        self._ctrl_tab = tabs[0]
        await self._ctrl_tab.attach()
        logger.info('Connected to control tab %s', self._ctrl_tab.id)
        for i in range(CONCURRENCY_PER_WORKER):
            await self._ctrl_tab.send({
                'method': 'Target.createTarget',
                'params': {
                    'url': 'about:blank'
                }
            })
            await self._ctrl_tab.recv()

        for tab in await self._rdp.debuggable_tabs():
            await self._idle_tabs.put(tab)

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

    async def shutdown(self):
        tabs = await self._rdp.debuggable_tabs()
        for tab in tabs:
            await self.close_tab(tab.id)

    async def render(self, url):
        tab = await self._idle_tabs.get()
        logger.debug('qsize after get: %d', self._idle_tabs.qsize())
        try:
            await tab.attach()
            await tab.listen()
            await tab.navigate(url)
            with timeout(PRERENDER_TIMEOUT):
                html = await tab.wait()
        finally:
            if tab.websocket:
                await tab.dettach()
            logger.debug('qsize before task_done: %d', self._idle_tabs.qsize())
            self._idle_tabs.task_done()
            logger.debug('qsize before put: %d', self._idle_tabs.qsize())
            await self._idle_tabs.put(tab)
            logger.debug('qsize after put: %d', self._idle_tabs.qsize())
        return html
