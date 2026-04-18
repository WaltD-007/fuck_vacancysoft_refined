"""Debug probe for a single generic_site board's location extraction.

Usage:
    python3 scripts/debug_generic_location.py <url>

Prints:
  * which candidate selector matched
  * outerHTML of the first matched job card
  * result of _sniff_location on the first 5 candidates
  * all location-hint selector matches in the page
  * first 5 records with listing_payload
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


def setup() -> None:
    repo = Path(__file__).resolve().parents[1]
    src = repo / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


async def main(url: str) -> None:
    setup()
    from playwright.async_api import async_playwright
    from vacancysoft.browser.session import browser_session
    import vacancysoft.adapters.generic_browser as gb

    print(f"[debug] url = {url}\n")

    async with async_playwright() as pw:
        async with browser_session(pw, headless=True) as (_browser, ctx):
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(3000)
            # Dismiss overlays like in the real adapter
            await page.evaluate("""() => {
                document.querySelectorAll(
                    '#system-ialert, [class*="ialert"], [class*="cookie-banner"], [class*="cookie-consent"], [class*="consent-banner"]'
                ).forEach(el => el.style.display = 'none');
            }""")
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, window.innerHeight)")
                await page.wait_for_timeout(500)

            # ── Scan CANDIDATE_LINK_SELECTORS ──
            print("[selectors] trying CANDIDATE_LINK_SELECTORS")
            hit_selector = None
            hit_elements: list = []
            for sel in gb.CANDIDATE_LINK_SELECTORS:
                try:
                    els = await page.query_selector_all(sel)
                except Exception:
                    continue
                if els:
                    print(f"  HIT: {sel!r} → {len(els)} elements")
                    if hit_selector is None:
                        hit_selector, hit_elements = sel, els
            if not hit_selector:
                # Fallback
                hit_elements = await page.query_selector_all(gb.FALLBACK_LINK_SELECTOR)
                print(f"  fallback a[href] → {len(hit_elements)} elements")

            # ── outerHTML of first match ──
            if hit_elements:
                first = hit_elements[0]
                try:
                    html = await first.evaluate("el => el.outerHTML")
                    print(f"\n[first element outerHTML (trimmed)]\n{html[:1200]}\n")
                except Exception:
                    pass

                # ── Walk up ancestors to show the card context ──
                try:
                    ancestor_html = await first.evaluate("""el => {
                        let cur = el;
                        for (let i = 0; i < 3 && cur && cur.parentElement; i++) cur = cur.parentElement;
                        return cur ? cur.outerHTML : '';
                    }""")
                    print(f"[ancestor (up to 3 levels) outerHTML (trimmed)]\n{ancestor_html[:1500]}\n")
                except Exception:
                    pass

                # ── Try _sniff_location on first 5 ──
                print("[_sniff_location output for first 5]")
                for i, el in enumerate(hit_elements[:5]):
                    try:
                        title = (await el.inner_text()).strip()
                    except Exception:
                        title = None
                    loc = await gb._sniff_location(el, title)
                    print(f"  [{i}] title={title!r:60}  location={loc!r}")

            # ── Global scan for LOCATION_HINT_SELECTORS ──
            print("\n[global scan for LOCATION_HINT_SELECTORS]")
            for sel in gb.LOCATION_HINT_SELECTORS:
                try:
                    els = await page.query_selector_all(sel)
                except Exception:
                    continue
                if not els:
                    continue
                samples = []
                for el in els[:4]:
                    try:
                        samples.append((await el.inner_text()).strip()[:80])
                    except Exception:
                        pass
                print(f"  {sel!r}: {len(els)}; samples={samples}")

            # ── Run the actual adapter ──
            print("\n[running full adapter]")
            from vacancysoft.adapters.generic_browser import GenericBrowserAdapter
            adapter = GenericBrowserAdapter()
            result = await adapter.discover({
                "job_board_url": url,
                "company": "probe",
                "max_pages": 1,
                "scroll_rounds": 1,
                "page_timeout_ms": 30_000,
            })
            print(f"  records: {len(result.jobs)}  "
                  f"with_location: {sum(1 for r in result.jobs if r.location_raw)}")
            print("  first 5:")
            for r in result.jobs[:5]:
                print(f"    title={r.title_raw!r:50}  loc={r.location_raw!r}")
                print(f"      payload: {json.dumps(r.listing_payload, default=str)[:300]}")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://careers.7im.co.uk/vacancies"
    asyncio.run(main(url))
