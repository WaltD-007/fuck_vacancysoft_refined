import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def uses_browserless() -> bool:
    return bool(os.getenv('BROWSERLESS_WS_URL', '').strip())


@asynccontextmanager
async def browser_session(
    playwright: Any,
    *,
    headless: bool = True,
    user_agent: str | None = None,
    viewport: dict[str, int] | None = None,
    extra_http_headers: dict[str, str] | None = None,
):
    browser = None
    context = None
    try:
        ws_url = os.getenv('BROWSERLESS_WS_URL', '').strip()
        if ws_url:
            try:
                browser = await asyncio.wait_for(
                    playwright.chromium.connect_over_cdp(ws_url),
                    timeout=15,
                )
            except (asyncio.TimeoutError, Exception) as exc:
                logger.warning("Browserless connection failed (%s), falling back to local Chrome", exc)
                browser = await playwright.chromium.launch(headless=headless)
        else:
            browser = await playwright.chromium.launch(headless=headless)

        context = await browser.new_context(
            user_agent=user_agent or _DEFAULT_USER_AGENT,
            viewport=viewport or {'width': 1280, 'height': 900},
            extra_http_headers=extra_http_headers or {},
        )
        yield browser, context
    finally:
        if context is not None:
            await context.close()
        if browser is not None:
            await browser.close()
