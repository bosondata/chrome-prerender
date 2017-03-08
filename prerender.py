#!/usr/bin/env python

import sys
import asyncio
import logging
import logging.config

from sanic import Sanic
from sanic import response
from sanic.exceptions import NotFound
from async_timeout import timeout

from chromerdp import ChromeRemoteDebugger

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'root': {
        'handlers': ['console'],
        'level': 'DEBUG'
    },
    'handlers': {
        'console': {
            'level': 'DEBUG',
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
logging.config.dictConfig(LOGGING)
logger = logging.getLogger(__name__)


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
        return await self._ctrl_tab.recv()

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
        with timeout(30):
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
    try:
        html = await prerender(request.app.prerender, url)
        return response.text(html)
    except asyncio.TimeoutError:
        return response.text('Gateway timeout', status=504)


@app.listener('after_server_start')
def after_server_start(app, loop):
    app.prerender = Prerender(loop=loop)
    loop.run_until_complete(app.prerender.connect())


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=8000, debug=True)
