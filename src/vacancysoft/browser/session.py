from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any

_SEMAPHORE = None
_LIMIT = None


def uses_browserless() -> bool:
    return bool(os.getenv('BROWSERLESS_WS_URL', '').strip())


def browserless_concurrency_limit() -> int:
    raw = os.getenv('BROWSERLESS_CONCURRENCY', '1') if uses_browserless() else os.getenv('PLAYWRIGHT_LOCAL_CONCURRENCY', '20')
    try:
        return max(1, int((raw or '').strip() or ('1' if uses_browserless() else '20')))
    except ValueError:
        return 1 if uses_browserless() else 20


def browser_semaphore() -> asyncio.Semaphore:
    global _SEMAPHORE, _LIMIT
    limit = browserless_concurrency_limit()
    if _SEMAPHORE is None or _LIMIT != limit:
        _SEMAPHORE = asyncio.Semaphore(limit)
        _LIMIT = limit
    return _SEMAPHORE


@asynccontextmanager
async def browser_session(playwright: Any, *, headless: bool = True):
    async with browser_semaphore():
        browser = None
        context = None
        try:
            ws_url = os.getenv('BROWSERLESS_WS_URL', '').strip()
            if ws_url:
                browser = await playwright.chromium.connect_over_cdp(ws_url)
            else:
                browser = await playwright.chromium.launch(headless=headless)
            context = await browser.new_context()
            yield browser, context
        finally:
            if context is not None:
                await context.close()
            if browser is not None:
                await browser.close()
