import math
import base64
import logging
import inspect
import asyncio
from asyncio import Future
from functools import partial
from typing import List, Dict, AnyStr, Set, Optional, Callable

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
            return Page(self, page, loop=self.loop)

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
    def __init__(self, debugger: ChromeRemoteDebugger, page_info: Dict, *, loop=None) -> None:
        self._debugger = debugger
        self.loop = loop
        self.id: str = page_info['id']
        self.websocket_debugger_url: str = page_info['webSocketDebuggerUrl']
        self.iteration: int = 0
        # TODO: detech window height using `Browser.getWindowForTarget` when it is available
        self._window_height: int = 600
        self._reset()

    def _reset(self) -> None:
        self._ws_task = None
        self._futures: Dict[str, Future] = {}
        self._callbacks: Dict[str, Callable[[Dict], None]] = {}
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self._request_id: int = 0

        self._render_future = self.loop.create_future()
        self._mhtml = MHTML()

        self._requests_sent: int = 0
        self._responses_received: Dict = {}
        self._res_body_request_ids: Dict = {}

    @property
    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def attach(self) -> None:
        logger.debug('Connecting to %s', self.websocket_debugger_url)
        self.websocket = await websockets.connect(
            self.websocket_debugger_url,
            max_size=5 * 2 ** 20,  # 5M
            loop=self.loop,
        )

        self.on('Inspector.detached', self._on_inspector_detached)
        self.on('Inspector.targetCrashed', self._on_inspector_target_crashed)
        self.on('Log.entryAdded', self._on_log_entry_added)
        self.on('Network.requestWillBeSent', self._on_request_will_be_sent)
        self.on('Network.responseReceived', self._on_response_received)
        self.on('Network.loadingFailed', self._on_response_received)

        self._ws_task = asyncio.ensure_future(self._listen())
        await asyncio.wait_for(self._enable_events(), timeout=5)

    async def detach(self) -> None:
        self._ws_task.cancel()
        await self.websocket.close()
        self._reset()

    async def _enable_events(self) -> None:
        futures = await asyncio.gather(
            self.send({'method': 'Page.enable'}),
            self.send({'method': 'Log.enable'}),
            self.send({'method': 'Network.enable'}),
            self.send({'method': 'Inspector.enable'}),
        )
        await asyncio.gather(*futures)

    def _remove_done_future(self, fut: Future, *, req_id: int) -> None:
        self._futures.pop(req_id, None)
        if not fut.cancelled() and fut.exception():
            self._render_future.set_exception(fut.exception())

    async def send(self, payload: Dict) -> Future:
        req_id = payload.get('id') or self._next_request_id
        payload['id'] = req_id
        future = self.loop.create_future()
        future.add_done_callback(partial(self._remove_done_future, req_id=req_id))
        self._futures[req_id] = future
        await self.websocket.send(json.dumps(payload))
        return future

    async def _handle_response(self, obj: Dict) -> None:
        req_id = obj.get('id')
        if req_id is not None:
            future = self._futures.get(req_id)
            if future and not future.cancelled():
                future.set_result(obj)
        method = obj.get('method')
        if method is not None:
            callback = self._callbacks.get(method)
            if callback is not None:
                ret = callback(obj)
                if inspect.isawaitable(ret):
                    await ret

    async def recv(self) -> asyncio.Task:
        res = await self.websocket.recv()
        obj = json.loads(res)
        return asyncio.ensure_future(self._handle_response(obj))

    def on(self, event: str, callback: Callable[[Future], None]) -> None:
        self._callbacks[event] = callback

    async def set_user_agent(self, ua: str) -> Future:
        return await self.send({
            'method': 'Network.setUserAgentOverride',
            'params': {'userAgent': ua}
        })

    async def navigate(self, url: str) -> Dict:
        if url != 'about:blank':
            self.iteration += 1
            logger.info('Page %s [%d] navigating to %s', self.id, self.iteration, url)
        future = await self.send({
            'method': 'Page.navigate',
            'params': {'url': url}
        })
        return await future

    async def evaluate(self, expr: str) -> Dict:
        future = await self.send({
            'method': 'Runtime.evaluate',
            'params': {'expression': expr}
        })
        return await future

    async def _evaluate_prerender_ready(self) -> bool:
        while True:
            res = await self.evaluate('window.prerenderReady == true')
            if res['result']['result'].get('value'):
                return True
            await asyncio.sleep(0.2)

    async def _wait_responses_ready(self) -> None:
        while True:
            if self._requests_sent > 0 and len(self._responses_received) >= self._requests_sent \
                    and len(self._res_body_request_ids) == 0:
                return
            await asyncio.sleep(0.5)

    async def _listen(self) -> None:
        tasks = []

        def _on_task_done(task: asyncio.Task) -> None:
            tasks.remove(task)
            if not task.cancelled() and task.exception():
                self._render_future.set_exception(task.exception())

        try:
            while True:
                task = await self.recv()
                task.add_done_callback(_on_task_done)
                tasks.append(task)
        finally:
            for task in tasks:
                task.cancel()

    async def render(self, url: str, format: str = 'html') -> AnyStr:
        self.on('Page.loadEventFired', partial(self._on_page_load_event_fired, format=format))
        self.on('Network.loadingFinished', partial(self._on_loading_finished, format=format))
        try:
            await self.navigate(url)
            return await self._render_future
        finally:
            self._callbacks.clear()
            self._futures.clear()

    def _on_request_will_be_sent(self, obj: Dict) -> None:
        redirect = obj['params'].get('redirectResponse')
        if not redirect:
            self._requests_sent += 1

    def _on_response_received(self, obj: Dict) -> None:
        self._responses_received[obj['params']['requestId']] = obj['params']
        logger.debug('Requests sent: %d, responses received: %d',
                    self._requests_sent, len(self._responses_received))

    def _on_inspector_detached(self, obj: Dict) -> None:
        # Chrome page destroyed
        raise TemporaryBrowserFailure('Inspector detached: {}'.format(obj['params']['reason']))

    def _on_inspector_target_crashed(self, obj: Dict) -> None:
        # Chrome page crashed
        raise TemporaryBrowserFailure('Inspector target crashed')

    def _on_log_entry_added(self, obj: Dict) -> None:
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

    async def _on_loading_finished(self, obj: Dict, *, format: str) -> None:
        if format == 'mhtml':
            await self.get_response_body(obj['params']['requestId'])

    async def _on_page_load_event_fired(self, obj: Dict, *, format: str) -> None:
        if format in ('mhtml', 'pdf'):
            await self._scroll_to_bottom()

        _done, pending = await asyncio.wait([
            self._evaluate_prerender_ready(),
            self._wait_responses_ready(),
        ], return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()

        if format == 'html':
            html = await self.get_html()
            self._render_future.set_result(html)
        elif format == 'mhtml':
            self._render_future.set_result(bytes(self._mhtml))
        elif format == 'pdf':
            data = await self.print_to_pdf()
            self._render_future.set_result(data)
        elif format == 'jpeg' or format == 'png':
            data = await self.screenshot(format)
            self._render_future.set_result(data)

    async def _scroll_to_bottom(self) -> None:
        # scroll to bottom to ensure images loaded
        height = await self.get_page_height()
        for i in range(math.ceil(height / self._window_height)):
            scroll_y = min(self._window_height * (i + 1), height)
            await self.evaluate('window.scrollTo(0, {})'.format(scroll_y))
            await asyncio.sleep(0.01)

    async def get_html(self) -> str:
        future = await self.send({
            'method': 'DOM.getDocument',
        })
        obj = await future
        node_id = obj['result']['root']['nodeId']

        future = await self.send({
            'method': 'DOM.getOuterHTML',
            'params': {'nodeId': node_id}
        })
        obj = await future
        html = obj['result']['outerHTML']
        return html

    async def get_response_body(self, request_id: str) -> None:
        req_id = self._next_request_id
        self._res_body_request_ids[req_id] = request_id
        future = await self.send({
            'id': req_id,
            'method': 'Network.getResponseBody',
            'params': {'requestId': request_id}
        })
        obj = await future
        body = obj['result'].get('body')
        if body is not None:
            base64_encoded = obj['result']['base64Encoded']
            request_id = self._res_body_request_ids[req_id]
            response = self._responses_received[request_id]['response']
            encoding = 'base64-encoded' if base64_encoded else 'quoted-printable'
            self._mhtml.add(response['url'], response['mimeType'], body, encoding)
        self._res_body_request_ids.pop(req_id)

    async def print_to_pdf(self) -> bytes:
        future = await self.send({
            'method': 'Page.printToPDF',
        })
        obj = await future
        data = base64.b64decode(obj['result']['data'])
        return data

    async def screenshot(self, format: str = 'png') -> bytes:
        future = await self.send({
            'method': 'Page.captureScreenshot',
            'params': {'format': format}
        })
        obj = await future
        data = base64.b64decode(obj['result']['data'])
        return data

    async def get_page_height(self) -> int:
        js = ('Math.max(document.body.scrollHeight, document.body.offsetHeight, '
              'document.documentElement.clientHeight, document.documentElement.scrollHeight, '
              'document.documentElement.offsetHeight)')
        res = await self.evaluate(js)
        return res['result']['result']['value']

    async def close(self) -> None:
        await self._debugger.close_page(self.id)

    def __repr__(self) -> str:
        return '<Page #{}>'.format(self.id)

    def __hash__(self) -> int:
        return hash(repr(self))
