# Prospero — Known Limitations

## Boards Returning 0 Jobs (Unsupported ATS / SPA)

| Company | URL | Platform | Issue |
|---------|-----|----------|-------|
| Wesleyan | https://careers.wesleyan.co.uk/vacancies | elementsuite | Full SPA, no job links in DOM or network JSON. 1 job visible but not scrapeable. |

## Adapter Gaps

- **elementsuite**: No adapter. SPA loads vacancy data via proprietary API with CSRF tokens.
- **SuccessFactors (classic)**: Only returns keyword-searched results, not full listings. May miss jobs outside search terms.
- **SuccessFactors (Cloudflare-protected)**: Requires Firefox fallback. Detection works but scraping may timeout.
- **National Grid**: Behind Cloudflare, uses generic_site + Firefox. May be flaky.

## Scraping Limitations

- **Pagination**: SuccessFactors adapter does not paginate. Only scrapes first page of results.
- **Cloudflare**: Some boards block both HTTP and Chromium. Firefox fallback works ~80% of the time.
- **SPAs with no API**: Pure JavaScript apps (React/Angular/Vue) that load jobs via proprietary APIs without standard selectors cannot be scraped by the generic adapter.

## UI / Frontend

- **Hydration warning**: Next.js SSR/CSR mismatch from Google Fonts link tag. Cosmetic only, no functional impact.

## Infrastructure

- **n8n webhook 403**: n8n Cloud blocks server-side requests. Workaround: browser calls webhooks directly.
- **localtunnel callback**: n8n → Prospero callback uses localtunnel (temporary). Needs permanent URL when deployed to Azure.
