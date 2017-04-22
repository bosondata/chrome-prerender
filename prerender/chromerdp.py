import logging
import asyncio
from typing import List, Dict

import ujson as json
import aiohttp
import websockets


logger = logging.getLogger(__name__)


class TemporaryBrowserFailure(Exception):
    pass


class ChromeRemoteDebugger:
    def __init__(self, host: str, port: int, loop=None):
        self._debugger_url = 'http://{}:{}'.format(host, port)
        self._session = aiohttp.ClientSession(loop=loop)
        self.loop = loop

    async def pages(self) -> List[Dict]:
        async with self._session.get('{}/json/list'.format(self._debugger_url)) as res:
            pages = await res.json(loads=json.loads)
            return pages

    async def debuggable_pages(self) -> List['Page']:
        pages = await self.pages()
        return [Page(self, page, loop=self.loop) for page in pages
                if 'webSocketDebuggerUrl' in page and page['type'] == 'page']

    async def new_page(self, url: str = None) -> 'Page':
        endpoint = '{}/json/new'.format(self._debugger_url)
        if url:
            endpoint = '{}?{}'.format(endpoint, url)
        async with self._session.get(endpoint) as res:
            page = await res.json(loads=json.loads)
            logger.info('Created new page %s', page['id'])
            return Page(self, page)

    async def close_page(self, page_id: str) -> None:
        async with self._session.get('{}/json/close/{}'.format(self._debugger_url, page_id)) as res:
            info = await res.text()
            logger.info('Closing page %s: %s', page_id, info)

    async def version(self) -> Dict:
        async with self._session.get('{}/json/version'.format(self._debugger_url)) as res:
            return await res.json(loads=json.loads)

    def shutdown(self) -> None:
        self._session.close()

    def __repr__(self) -> str:
        return '<ChromeRemoteDebugger@{}>'.format(self._debugger_url)


class Page:
    def __init__(self, debugger: ChromeRemoteDebugger, page_info: Dict, loop=None):
        self._debugger = debugger
        self.loop = loop
        self.id: str = page_info['id']
        self.websocket_debugger_url: str = page_info['webSocketDebuggerUrl']
        self.iteration: int = 0
        self._reset()

    def _reset(self) -> None:
        self.websocket = None
        self._request_id = 0
        self._get_html_request_id = -1
        self._eval_request_ids = set()
        self._load_event_fired = False
        self._prerender_ready = False
        self._get_document_request_id = -1

    @property
    def next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def attach(self) -> None:
        logger.debug('Connecting to %s', self.websocket_debugger_url)
        self.websocket = await websockets.connect(
            self.websocket_debugger_url,
            max_size=5 * 2 ** 20,  # 5M
            loop=self.loop,
        )

    async def detach(self) -> None:
        await self.websocket.close()
        self._reset()

    async def listen(self) -> None:
        await self.send({
            'id': self.next_request_id,
            'method': 'Page.enable'
        })
        await self.recv()
        await self.send({
            'id': self.next_request_id,
            'method': 'Log.enable'
        })
        await self.recv()

    async def send(self, payload: Dict):
        req_id = payload.get('id') or self.next_request_id
        payload['id'] = req_id
        return await self.websocket.send(json.dumps(payload))

    async def recv(self) -> Dict:
        res = await self.websocket.recv()
        return json.loads(res)

    async def set_user_agent(self, ua: str) -> None:
        await self.send({
            'method': 'Network.setUserAgentOverride',
            'params': {'userAgent': ua}
        })
        await self.recv()

    async def navigate(self, url: str) -> None:
        if url != 'about:blank':
            self.iteration += 1
            logger.info('Page %s [%d] navigating to %s', self.id, self.iteration, url)
        await self.send({
            'method': 'Page.navigate',
            'params': {'url': url}
        })
        await self.recv()

    async def evaluate(self, expr: str) -> None:
        request_id = self.next_request_id
        self._eval_request_ids.add(request_id)
        await self.send({
            'id': request_id,
            'method': 'Runtime.evaluate',
            'params': {'expression': expr}
        })

    async def wait(self) -> str:
        while True:
            obj = await self.recv()
            method = obj.get('method')
            if method == 'Inspector.detached':
                # Chrome page destroyed
                raise TemporaryBrowserFailure('Inspector detached: {}'.format(obj['params']['reason']))
            if method == 'Inspector.targetCrashed':
                # Chrome page crashed
                raise TemporaryBrowserFailure('Inspector target crashed')

            if method == 'Log.entryAdded':
                # Log browser console logs for debugging
                entry = obj['params']['entry']
                log_func = getattr(logger, entry['level'], None)
                if log_func:
                    resource_info = entry.get('url', '')
                    if entry.get('lineNumber'):
                        resource_info = '{}:{}'.format(resource_info, entry['lineNumber'])
                    log_func('%s console %s log %s: %s',
                             resource_info,
                             entry['source'],
                             entry['level'],
                             entry['text'])
                continue

            if method == 'Page.loadEventFired':
                self._load_event_fired = True
                continue

            if not self._prerender_ready and self._load_event_fired:
                await self.evaluate('window.prerenderReady == true')

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

    async def get_document(self) -> None:
        self._get_document_request_id = self.next_request_id
        await self.send({
            'id': self._get_document_request_id,
            'method': 'DOM.getDocument',
        })

    async def get_html(self, node_id: str) -> None:
        self._get_html_request_id = self.next_request_id
        await self.send({
            'id': self._get_html_request_id,
            'method': 'DOM.getOuterHTML',
            'params': {'nodeId': node_id}
        })

    async def close(self) -> None:
        await self._debugger.close_page(self.id)

    def __repr__(self) -> str:
        return '<Page #{}>'.format(self.id)

    def __hash__(self) -> int:
        return hash(repr(self))
