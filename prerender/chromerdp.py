
import logging
import asyncio

import ujson as json
import aiohttp
import websockets


logger = logging.getLogger(__name__)


class ChromeRemoteDebugger:
    def __init__(self, host, port, loop=None):
        self._debugger_url = 'http://{}:{}'.format(host, port)
        self._session = aiohttp.ClientSession(loop=loop)
        self.loop = loop

    async def tabs(self):
        async with self._session.get('{}/json/list'.format(self._debugger_url)) as res:
            tabs = await res.json(loads=json.loads)
            return tabs

    async def debuggable_tabs(self):
        tabs = await self.tabs()
        return [Tab(self, tab, loop=self.loop) for tab in tabs
                if 'webSocketDebuggerUrl' in tab and tab['type'] == 'page']

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

    async def version(self):
        async with self._session.get('{}/json/version'.format(self._debugger_url)) as res:
            return await res.json(loads=json.loads)

    def __del__(self):
        self._session.close()

    def __repr__(self):
        return '<ChromeRemoteDebugger@{}>'.format(self._debugger_url)


class Tab:
    def __init__(self, debugger, tab_info, loop=None):
        self._debugger = debugger
        self.loop = loop
        self.id = tab_info['id']
        self.websocket_debugger_url = tab_info['webSocketDebuggerUrl']
        self.iteration = 0
        self._reset()

    def _reset(self):
        self.websocket = None
        self._request_id = 0
        self._get_html_request_id = -1
        self._eval_request_ids = set()
        self._load_event_fired = False
        self._prerender_ready = False
        self._get_document_request_id = -1

    @property
    def next_request_id(self):
        self._request_id += 1
        return self._request_id

    async def attach(self):
        logger.debug('Connecting to %s', self.websocket_debugger_url)
        self.websocket = await websockets.connect(
            self.websocket_debugger_url,
            max_size=5 * 2 ** 20,  # 5M
            loop=self.loop,
        )

    async def dettach(self):
        await self.websocket.close()
        self._reset()

    async def listen(self):
        await self.send({
            'id': self.next_request_id,
            'method': 'Page.enable'
        })
        await self.recv()
        await self.send({
            'id': self.next_request_id,
            'method': 'Network.enable'
        })
        await self.recv()

    async def send(self, payload):
        req_id = payload.get('id') or self.next_request_id
        payload['id'] = req_id
        return await self.websocket.send(json.dumps(payload))

    async def recv(self):
        res = await self.websocket.recv()
        return json.loads(res)

    async def set_user_agent(self, ua):
        await self.send({
            'method': 'Network.setUserAgentOverride',
            'params': {'userAgent': ua}
        })
        await self.recv()

    async def navigate(self, url):
        if url != 'about:blank':
            self.iteration += 1
            logger.info('Tab %s [%d] navigating to %s', self.id, self.iteration, url)
        await self.send({
            'method': 'Page.navigate',
            'params': {'url': url}
        })
        await self.recv()

    async def evaluate(self, expr):
        request_id = self.next_request_id
        self._eval_request_ids.add(request_id)
        await self.send({
            'id': request_id,
            'method': 'Runtime.evaluate',
            'params': {'expression': expr}
        })

    async def wait(self):
        await self.send({
            'id': self.next_request_id,
            'method': 'Network.disable'
        })
        while True:
            obj = await self.recv()
            method = obj.get('method')
            if method == 'Page.loadEventFired':
                self._load_event_fired = True
            if not self._prerender_ready and self._load_event_fired:
                await self.evaluate('window.prerenderReady == true')
                await asyncio.sleep(0.5)
            req_id = obj.get('id')
            if req_id is None:
                continue
            if req_id in self._eval_request_ids:
                if obj['result']['result']['value']:
                    self._prerender_ready = True
                    self._eval_request_ids.clear()
                    await self.get_document()
            elif req_id == self._get_document_request_id:
                node_id = obj['result']['root']['nodeId']
                await self.get_html(node_id)
            elif req_id == self._get_html_request_id:
                html = obj['result']['outerHTML']
                return html

    async def get_document(self):
        self._get_document_request_id = self.next_request_id
        await self.send({
            'id': self._get_document_request_id,
            'method': 'DOM.getDocument',
        })

    async def get_html(self, node_id):
        self._get_html_request_id = self.next_request_id
        await self.send({
            'id': self._get_html_request_id,
            'method': 'DOM.getOuterHTML',
            'params': {'nodeId': node_id}
        })

    async def close(self):
        return await self._debugger.close_tab(self.id)

    def __repr__(self):
        return '<Tab #{}>'.format(self.id)
