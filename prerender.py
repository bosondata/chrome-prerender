#!/usr/bin/env python

import sys
import asyncio
import logging.config

from sanic import Sanic
from sanic import response
from sanic.exceptions import NotFound

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


async def prerender(url):
    rdp = ChromeRemoteDebugger('localhost', 9222)
    # tabs = await rdp.tabs()
    # tab = tabs[0]
    tab = await rdp.new_tab()
    await tab.attach()
    await tab.set_user_agent('Mozilla/5.0 (Linux) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/59.0.3033.0 Safari/537.36 Prerender (bosondata)')  # NOQA
    await tab.navigate(url)
    html = await tab.wait()
    await tab.close()
    return html


app = Sanic(__name__)


@app.exception(NotFound)
async def handle_request(request, exception):
    url = request.url
    if url.startswith('/http'):
        url = url[1:]
    html = await prerender(url)
    return response.text(html)


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=8000, debug=True)
