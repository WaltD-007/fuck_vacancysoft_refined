from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()

JOB_HINT_RE = re.compile(
    r"(job|career|vacanc|opening|opportunit|position|role|apply|recruit|posting)",
    re.IGNORECASE,
)


def add_repo_to_path(repo_root: Path) -> None:
    src_path = repo_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def trim(text: str | None, limit: int = 160) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    return text[: limit - 3] + "..." if len(text) > limit else text


async def dump_page_state(page) -> None:
    title = await page.title()
    url = page.url
    console.print(f"[bold]Loaded URL:[/bold] {url}")
    console.print(f"[bold]Page title:[/bold] {title}")

    counts = await page.evaluate(
        """
        () => {
            const anchors = Array.from(document.querySelectorAll("a"));
            const buttons = Array.from(document.querySelectorAll("button"));
            const forms = Array.from(document.querySelectorAll("form"));
            const iframes = Array.from(document.querySelectorAll("iframe"));
            const jobishLinks = anchors.filter(a => {
                const txt = (a.innerText || a.textContent || "").toLowerCase();
                const href = (a.getAttribute("href") || "").toLowerCase();
                return /(job|career|vacanc|opening|opportunit|position|role|apply|posting)/.test(txt + " " + href);
            });
            return {
                anchors: anchors.length,
                buttons: buttons.length,
                forms: forms.length,
                iframes: iframes.length,
                jobishLinks: jobishLinks.length,
            };
        }
        """
    )

    table = Table(title="DOM counts")
    table.add_column("Metric")
    table.add_column("Count", justify="right")
    for key, value in counts.items():
        table.add_row(key, str(value))
    console.print(table)

    samples = await page.evaluate(
        """
        () => {
            return Array.from(document.querySelectorAll("a"))
                .slice(0, 300)
                .map(a => ({
                    text: (a.innerText || a.textContent || "").trim(),
                    href: a.href || a.getAttribute("href") || ""
                }))
                .filter(x => x.text || x.href);
        }
        """
    )

    jobish = []
    for item in samples:
        hay = f"{item['text']} {item['href']}"
        if JOB_HINT_RE.search(hay):
            jobish.append(item)

    sample_table = Table(title="Job-like links found")
    sample_table.add_column("Text")
    sample_table.add_column("Href")
    for item in jobish[:20]:
        sample_table.add_row(trim(item["text"]), trim(item["href"], 120))
    console.print(sample_table)

    text_blob = await page.evaluate(
        """
        () => {
            const body = document.body ? document.body.innerText || "" : "";
            return body.slice(0, 4000);
        }
        """
    )
    console.print("\n[bold]Visible text sample:[/bold]")
    console.print(trim(text_blob, 1000))

    iframe_info = await page.evaluate(
        """
        () => Array.from(document.querySelectorAll("iframe")).map((f, i) => ({
            index: i,
            src: f.src || "",
            title: f.title || ""
        }))
        """
    )

    if iframe_info:
        iframe_table = Table(title="Iframes")
        iframe_table.add_column("Index")
        iframe_table.add_column("Title")
        iframe_table.add_column("Src")
        for item in iframe_info[:10]:
            iframe_table.add_row(str(item["index"]), trim(item["title"]), trim(item["src"], 120))
        console.print(iframe_table)


async def scroll_and_recount(page) -> None:
    for _ in range(5):
        await page.mouse.wheel(0, 2500)
        await page.wait_for_timeout(1000)

    counts = await page.evaluate(
        """
        () => {
            const anchors = Array.from(document.querySelectorAll("a"));
            const jobishLinks = anchors.filter(a => {
                const txt = (a.innerText || a.textContent || "").toLowerCase();
                const href = (a.getAttribute("href") || "").toLowerCase();
                return /(job|career|vacanc|opening|opportunit|position|role|apply|posting)/.test(txt + " " + href);
            });
            return {
                anchors: anchors.length,
                jobishLinks: jobishLinks.length,
                scrollY: window.scrollY
            };
        }
        """
    )
    console.print("\n[bold]After scrolling:[/bold]")
    console.print(counts)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Debug a generic job-board page in Playwright")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--url", required=True)
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--timeout-ms", type=int, default=30000)
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    add_repo_to_path(repo_root)

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not args.headful)
        page = await browser.new_page()

        console.print(f"[dim]Opening {args.url}[/dim]")
        await page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout_ms)
        await page.wait_for_timeout(2500)

        await dump_page_state(page)
        await scroll_and_recount(page)

        console.print("\n[bold]Attempting adapter run:[/bold]")
        try:
            from vacancysoft.adapters import GenericBrowserAdapter

            result = await GenericBrowserAdapter().discover(
                {
                    "job_board_url": args.url,
                    "company": "debug-company",
                    "search_terms": ["risk", "quant"],
                    "page_timeout_ms": args.timeout_ms,
                    "wait_after_nav_ms": 2000,
                }
            )
            console.print(f"[green]Adapter returned {len(result.jobs)} jobs[/green]")

            result_table = Table(title="Adapter sample results")
            result_table.add_column("Title")
            result_table.add_column("Location")
            result_table.add_column("URL")
            for job in result.jobs[:10]:
                result_table.add_row(
                    trim(getattr(job, "title_raw", None)),
                    trim(getattr(job, "location_raw", None)),
                    trim(getattr(job, "discovered_url", None), 120),
                )
            console.print(result_table)

            diag = getattr(result, "diagnostics", None)
            if diag is not None:
                console.print("\n[bold]Diagnostics:[/bold]")
                console.print(
                    {
                        "counters": dict(getattr(diag, "counters", {})),
                        "warnings": list(getattr(diag, "warnings", [])),
                        "errors": list(getattr(diag, "errors", [])),
                        "metadata": dict(getattr(diag, "metadata", {})),
                    }
                )
        except Exception as exc:
            console.print(f"[red]Adapter failed:[/red] {type(exc).__name__}: {exc}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())