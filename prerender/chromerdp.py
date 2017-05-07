import base64
import logging
import asyncio
from typing import List, Dict, AnyStr, Set, Optional

import ujson as json
import aiohttp
import websockets

from .mhtml import MHTML


logger = logging.getLogger(__name__)


class TemporaryBrowserFailure(Exception):
    pass


class ChromeRemoteDebugger:
    def __init__(self, host: str, port: int, loop=None) -> None:
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
    def __init__(self, debugger: ChromeRemoteDebugger, page_info: Dict, loop=None) -> None:
        self._debugger = debugger
        self.loop = loop
        self.id: str = page_info['id']
        self.websocket_debugger_url: str = page_info['webSocketDebuggerUrl']
        self.iteration: int = 0
        self._reset()

    def _reset(self) -> None:
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self._request_id: int = 0
        self._get_final_data_request_id: int = -1
        self._eval_request_ids: Set[int] = set()
        self._load_event_fired: bool = False
        self._prerender_ready: bool = False
        self._get_document_request_id: int = -1
        self._requests_sent: int = 0
        self._responses_received: Dict = {}
        self._res_body_request_ids: Dict = {}

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
        await self.send({
            'id': self.next_request_id,
            'method': 'Network.enable'
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

    async def evaluate(self, expr: str, check_value=True) -> None:
        request_id = self.next_request_id
        if check_value:
            self._eval_request_ids.add(request_id)
        await self.send({
            'id': request_id,
            'method': 'Runtime.evaluate',
            'params': {'expression': expr}
        })

    async def _handle_response(self, format: str, obj: Dict, mhtml: MHTML, future: asyncio.Future) -> None:
        method = obj.get('method')
        if method == 'Inspector.detached':
            # Chrome page destroyed
            raise TemporaryBrowserFailure('Inspector detached: {}'.format(obj['params']['reason']))
        elif method == 'Inspector.targetCrashed':
            # Chrome page crashed
            raise TemporaryBrowserFailure('Inspector target crashed')
        elif method == 'Log.entryAdded':
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
        elif method == 'Network.requestWillBeSent':
            redirect = obj['params'].get('redirectResponse')
            if not redirect:
                self._requests_sent += 1
        elif method == 'Network.responseReceived':
            self._responses_received[obj['params']['requestId']] = obj['params']
        elif method == 'Network.loadingFinished':
            if format == 'mhtml':
                await self.get_response_body(obj['params']['requestId'])
        elif method == 'Network.loadingFailed':
            self._responses_received[obj['params']['requestId']] = obj['params']
        elif method == 'Page.loadEventFired':
            self._load_event_fired = True

        if not self._prerender_ready and self._load_event_fired and self._requests_sent > 0 \
                and len(self._responses_received) >= self._requests_sent and len(self._res_body_request_ids) == 0:
            self._prerender_ready = True
            if format == 'html':
                await self.get_document()
            elif format == 'mhtml':
                future.set_result(bytes(mhtml))
            elif format == 'pdf':
                await self.evaluate('window.scrollTo(0, document.body.scrollHeight)', False)  # scroll to bottom
                await asyncio.sleep(1)
                await self.print_to_pdf()
            elif format == 'jpeg' or format == 'png':
                await self.screenshot()

        if not self._prerender_ready and self._load_event_fired:
            await self.evaluate('window.prerenderReady == true')

        req_id = obj.get('id')
        if req_id is None:
            return
        if req_id in self._eval_request_ids:
            if obj['result']['result']['value']:
                self._prerender_ready = True
                self._eval_request_ids.clear()
                if format == 'html':
                    await self.get_document()
                elif format == 'mhtml':
                    future.set_result(bytes(mhtml))
                elif format == 'pdf':
                    await self.print_to_pdf()
                elif format == 'jpeg' or format == 'png':
                    await self.screenshot()
        elif req_id in self._res_body_request_ids:
            body = obj['result'].get('body')
            if body is not None:
                base64_encoded = obj['result']['base64Encoded']
                if format == 'mhtml':
                    request_id = self._res_body_request_ids[req_id]
                    response = self._responses_received[request_id]['response']
                    encoding = 'base64-encoded' if base64_encoded else 'quoted-printable'
                    mhtml.add(response['url'], response['mimeType'], body, encoding)
            self._res_body_request_ids.pop(req_id)
        elif req_id == self._get_document_request_id:
            node_id = obj['result']['root']['nodeId']
            await self.get_html(node_id)
        elif req_id == self._get_final_data_request_id:
            if format == 'html':
                html = obj['result']['outerHTML']
                future.set_result(html)
            elif format in ('pdf', 'png', 'jpeg'):
                data = base64.b64decode(obj['result']['data'])
                future.set_result(data)

    async def _wait(self, format: str, future: asyncio.Future) -> None:
        mhtml = None
        if format == 'mhtml':
            mhtml = MHTML()
        while True:
            logger.debug('Requests sent: %d, responses received: %d',
                         self._requests_sent, len(self._responses_received))
            obj = await self.recv()
            asyncio.ensure_future(self._handle_response(format, obj, mhtml, future))

    async def wait(self, format: str = 'html') -> AnyStr:
        future = asyncio.Future()
        task = asyncio.ensure_future(self._wait(format, future))
        try:
            return await future
        finally:
            task.cancel()

    async def get_document(self) -> None:
        self._get_document_request_id = self.next_request_id
        await self.send({
            'id': self._get_document_request_id,
            'method': 'DOM.getDocument',
        })

    async def get_html(self, node_id: str) -> None:
        self._get_final_data_request_id = self.next_request_id
        await self.send({
            'id': self._get_final_data_request_id,
            'method': 'DOM.getOuterHTML',
            'params': {'nodeId': node_id}
        })

    async def get_response_body(self, request_id: str) -> None:
        req_id = self.next_request_id
        self._res_body_request_ids[req_id] = request_id
        await self.send({
            'id': req_id,
            'method': 'Network.getResponseBody',
            'params': {'requestId': request_id}
        })

    async def print_to_pdf(self) -> None:
        self._get_final_data_request_id = self.next_request_id
        await self.send({
            'id': self._get_final_data_request_id,
            'method': 'Page.printToPDF',
        })

    async def screenshot(self, format: str = 'png') -> None:
        self._get_final_data_request_id = self.next_request_id
        await self.send({
            'id': self._get_final_data_request_id,
            'method': 'Page.captureScreenshot',
            'params': {'format': format}
        })

    async def close(self) -> None:
        await self._debugger.close_page(self.id)

    def __repr__(self) -> str:
        return '<Page #{}>'.format(self.id)

    def __hash__(self) -> int:
        return hash(repr(self))
