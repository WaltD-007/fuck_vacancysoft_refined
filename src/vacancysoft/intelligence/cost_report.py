"""Cost + token comparison report for dossier and campaign runs.

Reads the per-row cost columns already written by ``dossier.py`` and
``campaign.py``, groups by inferred provider + model, and prints a
side-by-side table so the operator can compare OpenAI vs DeepSeek
runs after the fact.

Provider is inferred from ``model_used`` (or each entry in
``IntelligenceDossier.call_breakdown``) — no schema change needed:

  ``deepseek*``                  -> ``deepseek``
  ``gpt*`` / ``o1*`` / ``o3*`` / ``o4*`` / ``chatgpt*``  -> ``openai``
  anything else                  -> ``unknown``

Dossier rows are split across their two underlying LLM calls via the
``call_breakdown`` JSON column, so a dossier run with
``use_deepseek_for_dossier=true`` correctly shows its main call under
DeepSeek and its hiring-manager call under OpenAI.

Campaign rows record the one call that produced the final result.
When the campaign fallback fires, the primary call's tokens are not
captured today — see follow-up ticket 8 in docs/TODO.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from vacancysoft.db.models import CampaignOutput, IntelligenceDossier


# ── Provider inference ──────────────────────────────────────────────────


_OPENAI_PREFIXES = ("gpt", "o1", "o3", "o4", "chatgpt")
_DEEPSEEK_PREFIXES = ("deepseek",)


def infer_provider(model: str | None) -> str:
    """Classify a model string as ``openai`` / ``deepseek`` / ``unknown``.

    Intentionally tolerant: the DB may contain historical model names
    that have been renamed by the upstream provider. We key off the
    family prefix only.
    """
    if not model:
        return "unknown"
    m = model.lower().strip()
    if any(m.startswith(p) for p in _DEEPSEEK_PREFIXES):
        return "deepseek"
    if any(m.startswith(p) for p in _OPENAI_PREFIXES):
        return "openai"
    return "unknown"


# ── Window parsing ──────────────────────────────────────────────────────


def parse_since(spec: str) -> datetime | None:
    """Parse a ``--since`` argument.

    Accepts:
    - ``"all"`` → no cutoff (returns ``None``)
    - ``"7d"``  / ``"30d"`` / ``"6h"`` → relative window
    - ``"2025-04-01"`` → ISO date (UTC midnight)
    """
    if not spec or spec.lower() == "all":
        return None
    s = spec.strip().lower()
    if s.endswith("d") and s[:-1].isdigit():
        return datetime.now(timezone.utc) - timedelta(days=int(s[:-1]))
    if s.endswith("h") and s[:-1].isdigit():
        return datetime.now(timezone.utc) - timedelta(hours=int(s[:-1]))
    # ISO date
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(
            f"Could not parse --since={spec!r}. Use 'all', '7d', '30d', '6h', or an ISO date."
        ) from exc


# ── Aggregation ─────────────────────────────────────────────────────────


@dataclass
class Bucket:
    provider: str
    model: str
    count: int = 0
    tokens_prompt: int = 0
    tokens_completion: int = 0
    cost_usd: float = 0.0
    latency_ms_total: int = 0

    @property
    def tokens_total(self) -> int:
        return self.tokens_prompt + self.tokens_completion

    @property
    def avg_cost_usd(self) -> float:
        return self.cost_usd / self.count if self.count else 0.0

    @property
    def avg_latency_ms(self) -> int:
        return int(self.latency_ms_total / self.count) if self.count else 0


@dataclass
class Report:
    dossier_buckets: list[Bucket] = field(default_factory=list)
    campaign_buckets: list[Bucket] = field(default_factory=list)
    since: datetime | None = None
    dossier_rows: int = 0
    campaign_rows: int = 0

    def provider_totals(self) -> dict[str, dict[str, float]]:
        """Grand total per provider across dossier + campaign buckets."""
        totals: dict[str, dict[str, float]] = {}
        for b in self.dossier_buckets + self.campaign_buckets:
            t = totals.setdefault(b.provider, {"count": 0, "cost_usd": 0.0, "tokens": 0})
            t["count"] += b.count
            t["cost_usd"] += b.cost_usd
            t["tokens"] += b.tokens_total
        return totals


def build_report(session: Session, since: datetime | None = None) -> Report:
    """Load rows and bucket by (provider, model)."""
    # Dossier rows — iterate call_breakdown for per-call granularity
    d_query = select(IntelligenceDossier)
    if since is not None:
        # IntelligenceDossier.created_at is naive UTC; strip tzinfo so the
        # comparison works under both SQLite (naive) and Postgres (naive).
        d_query = d_query.where(IntelligenceDossier.created_at >= since.replace(tzinfo=None))
    dossier_rows = session.execute(d_query).scalars().all()

    dossier_map: dict[tuple[str, str], Bucket] = {}
    for d in dossier_rows:
        breakdown = d.call_breakdown or []
        if not breakdown:
            # No breakdown recorded — fall back to the aggregate row.
            _add_to_bucket(
                dossier_map,
                model=d.model_used or "",
                count=1,
                tokens_prompt=d.tokens_prompt or 0,
                tokens_completion=d.tokens_completion or 0,
                cost_usd=d.cost_usd or 0.0,
                latency_ms=d.latency_ms or 0,
            )
            continue
        for call in breakdown:
            _add_to_bucket(
                dossier_map,
                model=call.get("model") or "",
                count=1,
                tokens_prompt=call.get("tokens_prompt") or 0,
                tokens_completion=call.get("tokens_completion") or 0,
                cost_usd=call.get("cost_usd") or 0.0,
                latency_ms=call.get("latency_ms") or 0,
            )

    # Campaign rows
    c_query = select(CampaignOutput)
    if since is not None:
        c_query = c_query.where(CampaignOutput.created_at >= since.replace(tzinfo=None))
    campaign_rows = session.execute(c_query).scalars().all()

    campaign_map: dict[tuple[str, str], Bucket] = {}
    for c in campaign_rows:
        _add_to_bucket(
            campaign_map,
            model=c.model_used or "",
            count=1,
            tokens_prompt=c.tokens_prompt or 0,
            tokens_completion=c.tokens_completion or 0,
            cost_usd=c.cost_usd or 0.0,
            latency_ms=c.latency_ms or 0,
        )

    def _sort_buckets(m: dict[tuple[str, str], Bucket]) -> list[Bucket]:
        return sorted(m.values(), key=lambda b: (b.provider, -b.cost_usd))

    return Report(
        dossier_buckets=_sort_buckets(dossier_map),
        campaign_buckets=_sort_buckets(campaign_map),
        since=since,
        dossier_rows=len(dossier_rows),
        campaign_rows=len(campaign_rows),
    )


def _add_to_bucket(
    m: dict[tuple[str, str], Bucket],
    *,
    model: str,
    count: int,
    tokens_prompt: int,
    tokens_completion: int,
    cost_usd: float,
    latency_ms: int,
) -> None:
    provider = infer_provider(model)
    key = (provider, model or "(empty)")
    b = m.setdefault(key, Bucket(provider=provider, model=model or "(empty)"))
    b.count += count
    b.tokens_prompt += tokens_prompt
    b.tokens_completion += tokens_completion
    b.cost_usd += cost_usd
    b.latency_ms_total += latency_ms


# ── Rendering ───────────────────────────────────────────────────────────


def format_report(report: Report) -> str:
    """Plain-text table. No rich / tabulate dependency; safe for CI logs."""
    lines: list[str] = []
    if report.since is None:
        lines.append("Cost report (all time)")
    else:
        lines.append(f"Cost report since {report.since.isoformat(timespec='minutes')}")
    lines.append("=" * 76)
    lines.append("")

    lines.append(f"Dossiers — {report.dossier_rows} row(s), "
                 f"{sum(b.count for b in report.dossier_buckets)} LLM calls")
    lines.append(_render_table(report.dossier_buckets))
    lines.append("")

    lines.append(f"Campaigns — {report.campaign_rows} row(s), "
                 f"{sum(b.count for b in report.campaign_buckets)} LLM calls")
    lines.append(_render_table(report.campaign_buckets))
    lines.append("")

    lines.append("Totals by provider (dossier + campaign combined)")
    lines.append("-" * 60)
    totals = report.provider_totals()
    if not totals:
        lines.append("  (no data in window)")
    else:
        for provider in sorted(totals.keys()):
            t = totals[provider]
            lines.append(
                f"  {provider:<10}  calls={int(t['count']):>5}  "
                f"tokens={int(t['tokens']):>10,}  "
                f"cost=${t['cost_usd']:.4f}"
            )
    return "\n".join(lines)


def _render_table(buckets: list[Bucket]) -> str:
    if not buckets:
        return "  (no calls in window)"

    cols = ("Provider", "Model", "Calls", "Prompt", "Completion", "Total tok", "Cost USD", "Avg lat ms")
    rows = [
        (
            b.provider,
            b.model,
            str(b.count),
            f"{b.tokens_prompt:,}",
            f"{b.tokens_completion:,}",
            f"{b.tokens_total:,}",
            f"${b.cost_usd:.4f}",
            f"{b.avg_latency_ms:,}",
        )
        for b in buckets
    ]
    widths = [max(len(c), max(len(r[i]) for r in rows)) for i, c in enumerate(cols)]

    def _fmt(cells: tuple[str, ...]) -> str:
        return "  " + "  ".join(c.ljust(w) for c, w in zip(cells, widths))

    sep = "  " + "  ".join("-" * w for w in widths)
    out = [_fmt(cols), sep, *[_fmt(r) for r in rows]]
    return "\n".join(out)
