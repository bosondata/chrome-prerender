#!/usr/bin/env python

import asyncio

from chromerdp import ChromeRemoteDebugger


async def prerender():
    rdp = ChromeRemoteDebugger('localhost', 9222)
    tabs = await rdp.tabs()
    tab = tabs[0]
    await tab.attach()
    await tab.navigate('http://www.riskstorm.com')
    html = await tab.wait()
    print(html)


def main():
    loop = asyncio.get_event_loop()
    loop.run_until_complete(prerender())


if __name__ == '__main__':
    main()
