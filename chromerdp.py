
import logging
import asyncio

import ujson as json
import aiohttp
import websockets


logger = logging.getLogger(__name__)


class ChromeRemoteDebugger:
    def __init__(self, host, port):
        self._debugger_url = 'http://{}:{}'.format(host, port)
        self._session = aiohttp.ClientSession()

    async def tabs(self):
        async with self._session.get('{}/json/list'.format(self._debugger_url)) as res:
            tabs = await res.json(loads=json.loads)
            return [Tab(self, tab) for tab in tabs]

    async def new_tab(self, url=None):
        endpoint = '{}/json/new'.format(self._debugger_url)
        if url:
            endpoint = '{}?{}'.format(endpoint, url)
        async with self._session.get(endpoint) as res:
            tab = await res.json(loads=json.loads)
            return Tab(self, tab)

    async def close_tab(self, tab_id):
        async with self._session.get('{}/json/close/{}'.format(self._debugger_url, tab_id)) as res:
            return await res.text()

    def __del__(self):
        self._session.close()

    def __repr__(self):
        return '<ChromeRemoteDebugger@{}>'.format(self._debugger_url)


class Tab:
    def __init__(self, debugger, tab_info):
        self._debugger = debugger
        self.description = tab_info['description']
        self.id = tab_info['id']
        self.type = tab_info['type']
        self.title = tab_info['title']
        self.url = tab_info['url']
        self.devtools_frontend_url = tab_info['devtoolsFrontendUrl']
        self.websocket_debugger_url = tab_info['webSocketDebuggerUrl']
        self.websocket = None
        self._request_id = -1
        self._get_html_request_id = -1

    @property
    def next_request_id(self):
        self._request_id += 1
        return self._request_id

    async def attach(self):
        self.websocket = await websockets.connect(self.websocket_debugger_url)
        await self.websocket.send(json.dumps({
            'id': self.next_request_id,
            'method': 'Page.enable'
        }))
        await self.websocket.recv()
        await self.websocket.send(json.dumps({
            'id': self.next_request_id,
            'method': 'Network.enable'
        }))
        await self.websocket.recv()

    async def set_user_agent(self, ua):
        await self.websocket.send(json.dumps({
            'id': self.next_request_id,
            'method': 'Network.setUserAgentOverride',
            'params': {'userAgent': ua}
        }))
        await self.websocket.recv()

    async def navigate(self, url):
        await self.websocket.send(json.dumps({
            'id': self.next_request_id,
            'method': 'Page.navigate',
            'params': {'url': url}
        }))
        await self.websocket.recv()

    async def evaluate(self, expr):
        await self.websocket.send(json.dumps({
            'id': self.next_request_id,
            'method': 'Runtime.evaluate',
            'params': {'expression': expr}
        }))

    async def wait(self):
        while True:
            # await self.evaluate('window.prerenderReady === true')
            message = await self.websocket.recv()
            obj = json.loads(message)
            method = obj.get('method')
            if method == 'Page.loadEventFired':
                await self.get_html()
            req_id = obj.get('id')
            if req_id == self._get_html_request_id:
                html = obj['result']['outerHTML']
                return html

    async def get_html(self):
        await self.websocket.send(json.dumps({
            'id': self.next_request_id,
            'method': 'DOM.getDocument',
        }))
        res = await self.websocket.recv()
        # root_node_id = json.loads(res)['result']['root']['nodeId']
        root_node_id = 1
        self._get_html_request_id = self.next_request_id
        await self.websocket.send(json.dumps({
            'id': self._get_html_request_id,
            'method': 'DOM.getOuterHTML',
            'params': {'nodeId': root_node_id}
        }))

    async def close(self):
        return await self._debugger.close_tab(self.id)

    def __repr__(self):
        return '<Tab #{}>'.format(self.id)
