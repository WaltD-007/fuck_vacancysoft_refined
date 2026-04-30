"""
Auto-detect job board platform from a URL and return adapter config.
User pastes a careers URL → system figures out the adapter, slug, and company name.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import urlparse

import httpx


# ── SSRF defence ─────────────────────────────────────────────────────────────
# Every operator-supplied URL goes through validate_public_url() before any
# outbound request fires. Originally added (2026-04-26 security audit) when
# the API exposed POST /api/sources/detect and POST /api/sources for
# URL-driven adds — both now removed in favour of the CoreSignal-backed Add
# Company flow. Validator is retained because the same code path still serves
# the diagnose endpoint (re-detects platform on existing rows), the
# `prospero db add-source` CLI command, and the redetect_failing_sources
# maintenance script. Defence in depth, free to keep.
#
# Limitations to revisit if threat model widens:
#   - DNS rebinding: validator resolves once, the actual request may re-resolve
#     and get a different IP. Mitigation requires pinning the resolved IP into
#     the connection (custom transport).
#   - Playwright redirects (Step 3 of deep_detect_platform): browser navigation
#     follows server redirects without going through our event hook. The initial
#     URL is validated at function entry; mid-navigation redirects to internal
#     targets are NOT validated. Use a Playwright route handler to plug.

class UnsafeURLError(ValueError):
    """Raised when a URL fails the SSRF allow-list (non-HTTP scheme, no host,
    DNS failure, or resolves to a non-public IP). Callers should map this to
    400 Bad Request at the API boundary."""


async def validate_public_url(url: str) -> None:
    """Reject URLs that don't look like a normal public web request. Resolves
    every IP the host points to and rejects if any one is private / loopback /
    link-local / multicast / reserved / unspecified — covers IPv4 and IPv6.
    Raises UnsafeURLError on any rejection."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError(f"URL scheme not allowed: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError(f"URL has no host: {url!r}")
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Hostname did not resolve: {host!r} ({exc})") from exc
    for info in infos:
        addr_str = info[4][0]
        try:
            ip = ipaddress.ip_address(addr_str)
        except ValueError as exc:
            raise UnsafeURLError(f"Could not parse resolved address {addr_str!r}") from exc
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise UnsafeURLError(
                f"URL host {host!r} resolves to non-public address {addr_str}"
            )


async def _validate_outgoing_request(request: httpx.Request) -> None:
    """httpx event_hook — runs before every outbound request, including
    redirect targets. Ensures a public URL that 302s to an internal address
    can't slip through after the initial entry-point validation."""
    await validate_public_url(str(request.url))


# ── Pattern matchers (order matters — most specific first) ──────────────────

PLATFORM_PATTERNS: list[dict] = [
    # Workday: *.wd{N}.myworkdayjobs.com/...
    {
        "pattern": r"(?P<tenant>[\w-]+)\.wd\d+\.myworkdayjobs\.com/[\w-]+/(?P<site_path>[\w-]+)",
        "adapter": "workday",
        "extract": lambda m, url: {
            "tenant": m.group("tenant"),
            "site_path": m.group("site_path"),
        },
    },
    {
        "pattern": r"(?P<tenant>[\w-]+)\.wd\d+\.myworkdayjobs\.com",
        "adapter": "workday",
        "extract": lambda m, url: {"tenant": m.group("tenant")},
    },
    # Greenhouse: boards.greenhouse.io/{slug} or job-boards.greenhouse.io/{slug}
    {
        "pattern": r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?P<slug>[\w-]+)",
        "adapter": "greenhouse",
        "extract": lambda m, url: {"slug": m.group("slug")},
    },
    # Greenhouse embed: boards.greenhouse.io/embed/job_board/js?for={slug}
    {
        "pattern": r"greenhouse\.io/embed/job_board/.*for=(?P<slug>[\w-]+)",
        "adapter": "greenhouse",
        "extract": lambda m, url: {"slug": m.group("slug")},
    },
    # Lever: jobs.lever.co/{slug}
    {
        "pattern": r"jobs\.lever\.co/(?P<slug>[\w-]+)",
        "adapter": "lever",
        "extract": lambda m, url: {"slug": m.group("slug")},
    },
    # iCIMS: {slug}.icims.com
    {
        "pattern": r"(?P<slug>[\w-]+)\.icims\.com",
        "adapter": "icims",
        "extract": lambda m, url: {"slug": m.group("slug")},
    },
    # SmartRecruiters: careers.smartrecruiters.com/{company}
    {
        "pattern": r"careers\.smartrecruiters\.com/(?P<slug>[\w-]+)",
        "adapter": "smartrecruiters",
        "extract": lambda m, url: {"slug": m.group("slug")},
    },
    # Ashby: jobs.ashbyhq.com/{slug}
    {
        "pattern": r"jobs\.ashbyhq\.com/(?P<slug>[\w-]+)",
        "adapter": "ashby",
        "extract": lambda m, url: {"slug": m.group("slug")},
    },
    # Workable: apply.workable.com/{slug}
    {
        "pattern": r"apply\.workable\.com/(?P<slug>[\w-]+)",
        "adapter": "workable",
        "extract": lambda m, url: {"slug": m.group("slug")},
    },
    # Oracle Cloud: *.fa.*.oraclecloud.com
    {
        "pattern": r"[\w-]+\.fa\.[\w.]+\.oraclecloud\.com",
        "adapter": "oracle_cloud",
        "extract": lambda m, url: {},
    },
    # SuccessFactors: *.successfactors.com or performancemanager*.successfactors.com
    {
        "pattern": r"[\w.-]+\.successfactors\.(?:com|eu)",
        "adapter": "successfactors",
        "extract": lambda m, url: {},
    },
    # ADP: workforcenow.adp.com and careers.adp.com
    {
        "pattern": r"workforcenow\.adp\.com",
        "adapter": "adp",
        "extract": lambda m, url: {"ats_hint": "adp_workforcenow"},
    },
    {
        "pattern": r"careers\.adp\.com",
        "adapter": "adp",
        "extract": lambda m, url: {},
    },
    # Eightfold: *.eightfold.ai
    {
        "pattern": r"(?P<slug>[\w-]+)\.eightfold\.ai",
        "adapter": "eightfold",
        "extract": lambda m, url: {"slug": m.group("slug")},
    },
    # Pinpoint: *.pinpointhq.com
    {
        "pattern": r"(?P<slug>[\w-]+)\.pinpointhq\.com",
        "adapter": "pinpoint",
        "extract": lambda m, url: {"slug": m.group("slug")},
    },
    # HiBob: *.hibob.com
    {
        "pattern": r"[\w-]+\.hibob\.com",
        "adapter": "hibob",
        "extract": lambda m, url: {},
    },
    # Teamtailor: *.teamtailor.com
    {
        "pattern": r"(?P<slug>[\w-]+)\.teamtailor\.com",
        "adapter": "teamtailor",
        "extract": lambda m, url: {"slug": m.group("slug")},
    },
    # Taleo: *.taleo.net
    {
        "pattern": r"[\w.-]+\.taleo\.net",
        "adapter": "taleo",
        "extract": lambda m, url: {},
    },
]


def detect_platform(url: str) -> dict | None:
    """
    Given a URL, detect the job board platform and return adapter config.

    Returns dict with:
        adapter: str          — adapter name (e.g. "greenhouse")
        slug: str | None      — platform-specific identifier
        url: str              — cleaned URL
        config: dict          — any extra config extracted
    Or None if no platform detected (falls back to generic_site).
    """
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url

    parsed = urlparse(url)
    host_and_path = parsed.netloc + parsed.path

    for p in PLATFORM_PATTERNS:
        m = re.search(p["pattern"], host_and_path)
        if m:
            extra = p["extract"](m, url)
            return {
                "adapter": p["adapter"],
                "slug": extra.get("slug") or extra.get("tenant"),
                "url": url,
                "config": extra,
            }

    # No known platform — will use generic_site browser scraper
    return {
        "adapter": "generic_site",
        "slug": None,
        "url": url,
        "config": {},
    }


# ── Deep detection: follow job links to find the real platform ──────────────

# Patterns in page HTML that hint at an embedded ATS
_EMBED_HINTS = [
    (r'(?:src|href)=["\']([^"\']*myworkdayjobs\.com[^"\']*)', "workday"),
    (r'greenhouse\.io/embed/job_board/.*?for=([\w-]+)', "greenhouse_embed"),
    (r'(?:src|href)=["\']([^"\']*greenhouse\.io[^"\']*)', "greenhouse"),
    (r'(?:src|href)=["\']([^"\']*lever\.co[^"\']*)', "lever"),
    (r'(?:src|href)=["\']([^"\']*icims\.com[^"\']*)', "icims"),
    (r'(?:src|href)=["\']([^"\']*smartrecruiters\.com[^"\']*)', "smartrecruiters"),
    (r'(?:src|href)=["\']([^"\']*ashbyhq\.com[^"\']*)', "ashby"),
    (r'(?:src|href)=["\']([^"\']*workable\.com[^"\']*)', "workable"),
    (r'(?:src|href)=["\']([^"\']*oraclecloud\.com[^"\']*)', "oracle_cloud"),
    (r'(?:src|href)=["\']([^"\']*successfactors\.[^"\']*)', "successfactors"),
    (r'(?:src|href)=["\']([^"\']*eightfold\.ai[^"\']*)', "eightfold"),
    (r'(?:src|href)=["\']([^"\']*workforcenow\.adp\.com[^"\']*)', "adp_workforcenow"),
    (r'(?:src|href)=["\']([^"\']*taleo\.net[^"\']*)', "taleo"),
    (r'(?:src|href)=["\']([^"\']*pinpointhq\.com[^"\']*)', "pinpoint"),
    (r'(?:src|href)=["\']([^"\']*hibob\.com[^"\']*)', "hibob"),
]

# Common href patterns that look like job links
_JOB_LINK_RE = re.compile(
    r'href=["\']([^"\']+(?:/jobs?/|/vacanc|/position|/careers?/|/opening|/posting)[^"\']*)',
    re.IGNORECASE,
)


async def deep_detect_platform(url: str, timeout: float = 20.0) -> dict | None:
    """
    Fetch the page HTML and look for embedded ATS links or iframes.
    If found, detect the platform from the embedded URL.
    If not, follow the first job-looking link and check where it redirects.

    Returns the same format as detect_platform, or None if nothing found.
    """
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url

    # SSRF defence — validate before any request. Raises UnsafeURLError,
    # which the route layer maps to 400. We deliberately let it propagate
    # past the broad except-blocks below (see UnsafeURLError re-raises).
    await validate_public_url(url)

    http_blocked = False
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            event_hooks={"request": [_validate_outgoing_request]},
        ) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                http_blocked = True
                raise Exception(f"HTTP {resp.status_code}")
            html = resp.text

            # Step 1: scan page HTML for embedded ATS URLs (iframes, links, scripts)
            for pattern, platform in _EMBED_HINTS:
                m = re.search(pattern, html, re.IGNORECASE)
                if m:
                    # Special case: greenhouse embed extracts slug directly
                    if platform == "greenhouse_embed":
                        slug = m.group(1)
                        board_url = f"https://boards.greenhouse.io/{slug}"
                        return {
                            "adapter": "greenhouse",
                            "slug": slug,
                            "url": board_url,
                            "root_url": url,
                            "config": {},
                            "detected_via": "greenhouse_embed",
                        }

                    embedded_url = m.group(1)
                    # Skip static assets (CSS, images, fonts, icons)
                    lower_url = embedded_url.lower()
                    if any(lower_url.endswith(ext) for ext in (".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".eot")):
                        continue
                    if "/css/" in lower_url or "/fonts/" in lower_url or "/images/" in lower_url or "/icons/" in lower_url:
                        continue
                    # Make absolute if relative
                    if embedded_url.startswith("//"):
                        embedded_url = "https:" + embedded_url
                    elif embedded_url.startswith("/"):
                        parsed = urlparse(url)
                        embedded_url = f"{parsed.scheme}://{parsed.netloc}{embedded_url}"
                    detection = detect_platform(embedded_url)
                    if detection and detection["adapter"] != "generic_site":
                        detection["root_url"] = url
                        detection["detected_via"] = "embed_scan"
                        return detection
                    # Even if detect_platform didn't match, we know the platform
                    return {
                        "adapter": platform,
                        "slug": None,
                        "url": embedded_url,
                        "root_url": url,
                        "config": {},
                        "detected_via": "embed_hint",
                    }

            # Step 2: find a job link on the page and follow it
            job_links = _JOB_LINK_RE.findall(html)
            for href in job_links[:5]:
                # Make absolute
                if href.startswith("/"):
                    parsed = urlparse(url)
                    href = f"{parsed.scheme}://{parsed.netloc}{href}"
                elif not href.startswith("http"):
                    continue

                # Skip links back to the same domain (not useful)
                if urlparse(href).netloc == urlparse(url).netloc:
                    # But still follow it — it might redirect to the ATS
                    pass

                try:
                    job_resp = await client.get(href, follow_redirects=True)
                    final_url = str(job_resp.url)
                    detection = detect_platform(final_url)
                    if detection and detection["adapter"] != "generic_site":
                        # Found the real platform by following a job link
                        detection["root_url"] = url
                        detection["detected_via"] = "job_link_follow"
                        return detection
                except UnsafeURLError:
                    # Page contained a link to an internal address — skip
                    # this link, keep trying others. Don't propagate; one
                    # bad link doesn't mean the original URL is unsafe.
                    continue
                except Exception:
                    continue

    except UnsafeURLError:
        raise  # SSRF defence — never silenced by the fallback path
    except Exception:
        pass

    # Step 3: if HTTP failed (bot protection), try with a browser
    # Try Chromium first; if Cloudflare detected, retry with Firefox
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browsers_to_try = [("chromium", pw.chromium)]
            if http_blocked:
                # Skip Chromium for known 403s — go straight to Firefox
                browsers_to_try = [("firefox", pw.firefox)]
            else:
                browsers_to_try = [("chromium", pw.chromium), ("firefox", pw.firefox)]

            for browser_name, browser_type in browsers_to_try:
                browser = await browser_type.launch(headless=True)
                ctx = await browser.new_context(
                    viewport={"width": 1280, "height": 900},
                )
                page = await ctx.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(3000)

                    # Check for Cloudflare block — retry with Firefox
                    page_title = (await page.title()).lower()
                    if browser_name == "chromium" and ("attention required" in page_title or "just a moment" in page_title):
                        await page.close()
                        await browser.close()
                        continue  # retry with Firefox

                    html = await page.content()

                    # Scan rendered HTML for ATS embeds
                    for pattern, platform in _EMBED_HINTS:
                        m = re.search(pattern, html, re.IGNORECASE)
                        if m:
                            embedded_url = m.group(1)
                            lower_url = embedded_url.lower()
                            if any(lower_url.endswith(ext) for ext in (".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".eot")):
                                continue
                            if "/css/" in lower_url or "/fonts/" in lower_url or "/images/" in lower_url or "/icons/" in lower_url:
                                continue
                            if embedded_url.startswith("//"):
                                embedded_url = "https:" + embedded_url
                            elif embedded_url.startswith("/"):
                                parsed = urlparse(url)
                                embedded_url = f"{parsed.scheme}://{parsed.netloc}{embedded_url}"
                            detection = detect_platform(embedded_url)
                            if detection and detection["adapter"] != "generic_site":
                                detection["root_url"] = url
                                detection["detected_via"] = f"{browser_name}_embed_scan"
                                return detection
                            return {
                                "adapter": platform, "slug": None,
                                "url": embedded_url, "root_url": url,
                                "config": {}, "detected_via": f"{browser_name}_embed_hint",
                            }

                    # Follow first job link via browser
                    job_links = await page.eval_on_selector_all(
                        "a[href]",
                        """els => els.map(e => e.href).filter(h =>
                            /\\/jobs?\\/|vacanc|position|careers?\\/|opening|posting/i.test(h)
                        ).slice(0, 3)"""
                    )
                    for href in job_links:
                        try:
                            # Validate before navigating — Playwright's request
                            # path is separate from our httpx event hook, so
                            # internal-IP follow-throughs from a hostile careers
                            # page would otherwise slip through.
                            await validate_public_url(href)
                            await page.goto(href, wait_until="domcontentloaded", timeout=10000)
                            final_url = page.url
                            detection = detect_platform(final_url)
                            if detection and detection["adapter"] != "generic_site":
                                detection["root_url"] = url
                                detection["detected_via"] = f"{browser_name}_job_link_follow"
                                return detection
                        except UnsafeURLError:
                            continue
                        except Exception:
                            continue

                    # If we got here with content, no need to retry
                    break
                finally:
                    await page.close()
                    await browser.close()
    except Exception:
        pass

    return None


def _slug_to_company_name(slug: str | None) -> str:
    """Convert a slug like 'marathon-asset-management' to 'Marathon Asset Management'."""
    if not slug:
        return ""
    return slug.replace("-", " ").replace("_", " ").title()


async def detect_and_validate(url: str, timeout: float = 30.0) -> dict:
    """
    Detect platform, then do a quick HTTP check to confirm the URL is reachable.
    For API-based adapters (greenhouse, lever), hit the API to get a job count.

    Raises:
        UnsafeURLError: if the URL points to a non-public host (SSRF defence).
            The route layer maps this to 400 Bad Request.

    Returns:
        adapter: str
        slug: str | None
        url: str
        company_guess: str
        reachable: bool
        job_count: int | None    — if we can quickly determine it
        error: str | None
    """
    # SSRF defence — single entry-point check that protects every remaining
    # caller: /api/sources/{id}/diagnose, the CLI `db add-source`, and
    # scripts/redetect_failing_sources.py. The route layer maps UnsafeURLError
    # to 400 Bad Request.
    await validate_public_url(url)

    detection = detect_platform(url)

    # If initial detection is generic, try deep detection (scan page for embedded ATS)
    if detection["adapter"] == "generic_site":
        deep = await deep_detect_platform(url, timeout=timeout)
        if deep and deep.get("adapter") != "generic_site":
            detection = deep
            # Use the ATS URL for scraping, not the company root
            if "url" in deep:
                detection["url"] = deep["url"]

    adapter = detection["adapter"]
    slug = detection["slug"]
    company_guess = _slug_to_company_name(slug)

    result = {
        "adapter": adapter,
        "slug": slug,
        "url": detection["url"],
        "company_guess": company_guess,
        "reachable": False,
        "job_count": None,
        "error": None,
    }

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            event_hooks={"request": [_validate_outgoing_request]},
        ) as client:
            # For Greenhouse, hit the API directly for a quick job count
            if adapter == "greenhouse" and slug:
                api_url = f"https://api.greenhouse.io/v1/boards/{slug}/jobs"
                resp = await client.get(api_url)
                if resp.status_code == 200:
                    data = resp.json()
                    result["reachable"] = True
                    result["job_count"] = len(data.get("jobs", []))
                    # Try to get company name from first job
                    jobs = data.get("jobs", [])
                    if jobs and not company_guess:
                        co = jobs[0].get("company", {})
                        result["company_guess"] = co.get("name", company_guess)
                elif resp.status_code == 404:
                    result["error"] = f"Greenhouse board '{slug}' not found (404)"
                else:
                    result["error"] = f"Greenhouse API returned {resp.status_code}"
                return result

            # For Lever, hit the API
            if adapter == "lever" and slug:
                api_url = f"https://api.lever.co/v0/postings/{slug}"
                resp = await client.get(api_url)
                if resp.status_code == 200:
                    data = resp.json()
                    result["reachable"] = True
                    result["job_count"] = len(data) if isinstance(data, list) else None
                else:
                    result["error"] = f"Lever API returned {resp.status_code}"
                return result

            # For iCIMS, try the in_iframe HTML
            if adapter == "icims" and slug:
                iframe_url = f"https://{slug}.icims.com/jobs/search?in_iframe=1"
                resp = await client.get(iframe_url)
                if resp.status_code == 200:
                    result["reachable"] = True
                    # Count job links
                    import re as _re
                    job_links = _re.findall(r'/jobs/\d+/[^"]+/job', resp.text)
                    result["job_count"] = len(set(job_links))
                else:
                    result["error"] = f"iCIMS returned {resp.status_code}"
                return result

            # Default: just check if URL is reachable
            resp = await client.get(detection["url"])
            result["reachable"] = resp.status_code < 400
            if resp.status_code >= 400:
                # Try browser fallback for 403/Cloudflare
                browser_ok = await _browser_reachability_check(detection["url"])
                if browser_ok:
                    result["reachable"] = True
                    result["error"] = None
                else:
                    result["error"] = f"HTTP {resp.status_code}"

    except httpx.TimeoutException:
        result["error"] = "Timeout"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"

    return result


async def _browser_reachability_check(url: str) -> bool:
    """Use Firefox to check if a 403'd URL is actually reachable (Cloudflare bypass)."""
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.firefox.launch(headless=True)
            ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(3000)
                title = (await page.title()).lower()
                # Still blocked
                if "attention required" in title or "just a moment" in title or "access denied" in title:
                    return False
                return True
            finally:
                await page.close()
                await ctx.close()
                await browser.close()
    except Exception:
        return False


async def firefox_fetch(url: str, *, timeout_ms: int = 15000, body_limit_chars: int = 30000) -> dict | None:
    """Fetch a URL via headless Firefox. Used by the diagnose endpoint
    when httpx hits a 403 (Cloudflare / Akamai bot detection) — Firefox's
    JA3 fingerprint and standard headers get past most CDN bot blocks
    that reject httpx.

    Returns:
        {
            "status": int,                # final HTTP status (200 if page rendered)
            "url": str,                   # final URL after any 30x redirects
            "body": str,                  # first body_limit_chars chars of HTML
            "blocked_by_challenge": bool, # True if Cloudflare/Akamai challenge title detected
        }
        or None on launch failure / unreachable.

    Cleanup is in finally — leaked browsers compound until the API OOMs.
    """
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return None

    try:
        async with async_playwright() as pw:
            browser = await pw.firefox.launch(headless=True)
            try:
                ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
                page = await ctx.new_page()
                try:
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    # 3s hold gives JS challenges (Cloudflare's "just a moment")
                    # time to resolve and redirect to the real page.
                    await page.wait_for_timeout(3000)
                    title = (await page.title()).lower()
                    blocked = (
                        "attention required" in title
                        or "just a moment" in title
                        or "access denied" in title
                    )
                    body = await page.content()
                    return {
                        "status": response.status if response is not None else 0,
                        "url": page.url,
                        "body": body[:body_limit_chars],
                        "blocked_by_challenge": blocked,
                    }
                finally:
                    await page.close()
                    await ctx.close()
            finally:
                await browser.close()
    except Exception:
        return None
