from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table
from playwright.async_api import async_playwright

console = Console()


def add_repo_to_path(repo_root: Path) -> None:
    src_path = repo_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def clean(text: str | None, limit: int = 160) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    return text[: limit - 3] + "..." if len(text) > limit else text


async def main() -> None:
    repo_root = Path(".").resolve()
    add_repo_to_path(repo_root)

    from vacancysoft.browser.session import browser_session

    url = "https://careers.arbuthnotlatham.co.uk/vacancies"

    async with async_playwright() as playwright:
        async with browser_session(playwright, headless=True) as (_browser, context):
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(4000)

            console.print(f"[bold]Final URL:[/bold] {page.url}")
            console.print(f"[bold]Title:[/bold] {await page.title()}")

            visible_text = await page.evaluate(
                """
                () => (document.body?.innerText || "").slice(0, 3000)
                """
            )
            console.print("\n[bold]Visible text sample:[/bold]")
            console.print(clean(visible_text, 1200))

            anchors = await page.evaluate(
                """
                () => Array.from(document.querySelectorAll("a")).slice(0, 400).map(a => ({
                    text: (a.innerText || a.textContent || "").trim(),
                    href: a.href || a.getAttribute("href") || "",
                    cls: a.className || ""
                }))
                """
            )

            table = Table(title="Anchor sample")
            table.add_column("Text")
            table.add_column("Href")
            table.add_column("Class")
            count = 0
            for item in anchors:
                text = clean(item["text"])
                href = clean(item["href"], 120)
                cls = clean(str(item["cls"]), 80)
                if not text and not href:
                    continue
                table.add_row(text, href, cls)
                count += 1
                if count >= 40:
                    break
            console.print(table)

            cards = await page.evaluate(
                """
                () => {
                    const selectors = [
                        "[class*='job']",
                        "[class*='vacan']",
                        "[class*='career']",
                        "[class*='role']",
                        "[class*='opportunit']",
                        "article",
                        "li",
                        "tr"
                    ];
                    const found = [];
                    for (const selector of selectors) {
                        for (const el of Array.from(document.querySelectorAll(selector)).slice(0, 50)) {
                            const txt = (el.innerText || el.textContent || "").trim();
                            if (!txt) continue;
                            found.push({
                                selector,
                                text: txt.slice(0, 300)
                            });
                            if (found.length >= 40) return found;
                        }
                    }
                    return found;
                }
                """
            )

            cards_table = Table(title="Candidate container sample")
            cards_table.add_column("Selector")
            cards_table.add_column("Text")
            for item in cards[:40]:
                cards_table.add_row(item["selector"], clean(item["text"], 180))
            console.print(cards_table)

            await page.close()


if __name__ == "__main__":
    asyncio.run(main())