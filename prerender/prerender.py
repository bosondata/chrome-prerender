import os
import asyncio
import logging
from multiprocessing import cpu_count

from async_timeout import timeout
from websockets.exceptions import InvalidHandshake, ConnectionClosed

from .chromerdp import ChromeRemoteDebugger

logger = logging.getLogger(__name__)

PRERENDER_TIMEOUT = int(os.environ.get('PRERENDER_TIMEOUT', 30))
CONCURRENCY_PER_WORKER = int(os.environ.get('CONCURRENCY', cpu_count() * 2))
MAX_ITERATIONS = int(os.environ.get('ITERATIONS', 200))


class Prerender:
    def __init__(self, host='localhost', port=9222, loop=None):
        self.host = host
        self.port = port
        self.loop = loop
        self._rdp = ChromeRemoteDebugger(host, port, loop=loop)
        self._tab_ids = set()
        self._idle_tabs = asyncio.Queue(loop=self.loop)

    async def bootstrap(self):
        for i in range(CONCURRENCY_PER_WORKER):
            tab = await self._rdp.new_tab()
            await self._idle_tabs.put(tab)
            self._tab_ids.add(tab.id)

    async def tabs(self):
        return await self._rdp.tabs()

    async def version(self):
        return await self._rdp.version()

    async def shutdown(self):
        for tab_id in self._tab_ids:
            await self._rdp.close_tab(tab_id)

    async def render(self, url):
        if not self._tab_ids:
            raise RuntimeError('No browser available')

        tab = await self._idle_tabs.get()
        reopen = False
        try:
            await tab.attach()
            await tab.listen()
            await tab.navigate(url)
            with timeout(PRERENDER_TIMEOUT):
                html = await tab.wait()
            return html
        except InvalidHandshake:
            logger.error('Chrome invalid handshake for tab %s', tab.id)
            reopen = True
            raise
        except ConnectionClosed:
            logger.error('Chrome remote connection closed for tab %s', tab.id)
            reopen = True
            raise
        except RuntimeError as e:
            # https://github.com/MagicStack/uvloop/issues/68
            if 'unable to perform operation' in str(e):
                reopen = True
            raise
        finally:
            if tab.websocket:
                try:
                    await tab.navigate('about:blank')
                    await tab.detach()
                except Exception:
                    logger.exception('Error detaching from tab %s', tab.id)
                    reopen = True
            self._idle_tabs.task_done()
            await self._manage_tab(tab, reopen)

    async def _manage_tab(self, tab, reopen=False):
        if not reopen and tab.iteration < MAX_ITERATIONS:
            await self._idle_tabs.put(tab)
            return

        await tab.close()
        self._tab_ids.remove(tab.id)
        tab = await self._rdp.new_tab()
        # wait until Chrome is ready
        await asyncio.sleep(0.5)
        await self._idle_tabs.put(tab)
        self._tab_ids.add(tab.id)
