"""The Weekly pulse tab (Task 7.3): the week's latest run, rebuilt from its saved artifacts.

The view goes through `build_context`, the same function `render.py` used to write `pulse.md`, so the
page shows exactly what was validated. Quotes are verbatim spans of scrubbed review text.
"""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from pulse.analysis.aggregate import Aggregates, InsufficientData, ThemeStats, rank_top
from pulse.config import Settings
from pulse.llm.compose import PulseDraft
from pulse.models import Quote, Taxonomy
from pulse.report.render import PulseContext, build_context
from pulse.report.words import count_words
from pulse.store import Store
from pulse.timeutil import Window

GATES = tuple(f"V{number}" for number in range(1, 8))
PULSE_ARTIFACTS = ("aggregates.json", "taxonomy.json", "quotes.json", "pulse_draft.json")


@dataclass(frozen=True)
class Readiness:
    """Whether Publish and Draft may run: failing `pulse doctor` checks, and whether a draft tool exists."""

    problems: list[tuple[str, str]]  # (check name, detail)
    draft_supported: bool


def pulse_view(store: Store, settings: Settings, iso_week: str) -> dict[str, Any] | None:
    """The week's newest run that has a pulse (else its latest run) as the page shows it, or None when the
    week has no run at all. A later backfill run that stopped early doesn't hide a validated pulse."""
    latest = store.latest_run(iso_week)
    if latest is None:
        return None
    run = latest_with_pulse(store, settings, iso_week) or latest
    run_dir = Path(settings.paths.runs) / run["run_id"]
    delivery = store.get_delivery(iso_week)
    view: dict[str, Any] = {
        "run_id": run["run_id"],
        "iso_week": iso_week,
        "status": run["status"],
        "latest_status": latest["status"],
        "created_at": run["created_at"],
        "delivery": {
            "doc_url": delivery.doc_url if delivery else None,
            "draft_id": delivery.draft_id if delivery else None,
        },
    }
    ctx = _context(run_dir, _window(run["window"]), settings)
    markdown_path = run_dir / "pulse.md"
    if ctx is None or not markdown_path.is_file():
        return view | {"has_pulse": False}
    markdown = markdown_path.read_text(encoding="utf-8")
    fields = _pulse_fields(ctx, markdown, run_dir)
    return view | {"has_pulse": True, "markdown": markdown, "max_words": settings.pulse.max_words, **fields}


def latest_with_pulse(store: Store, settings: Settings, iso_week: str) -> dict[str, Any] | None:
    for run_id in store.run_ids_for_week(iso_week):
        run_dir = Path(settings.paths.runs) / run_id
        if (run_dir / "pulse.md").is_file() and all((run_dir / name).is_file() for name in PULSE_ARTIFACTS):
            return store.get_run(run_id)
    return None


def _window(data: dict[str, str]) -> Window:
    return Window(
        start=date.fromisoformat(data["start"]),
        end=date.fromisoformat(data["end"]),
        iso_week=data["iso_week"],
        timezone=data["timezone"],
    )


def _context(run_dir: Path, window: Window, settings: Settings) -> PulseContext | None:
    """The rendered pulse rebuilt from the run's artifacts, or None when the run never got that far."""
    if not all((run_dir / name).is_file() for name in PULSE_ARTIFACTS):
        return None
    data = _load(run_dir, "aggregates.json")
    themes = [ThemeStats(**stats) for stats in data.pop("themes")]
    data.pop("window")
    agg = Aggregates(window=window, themes=themes, **data)
    try:
        top = rank_top(agg, settings.themes.top_n)
    except InsufficientData:
        return None
    return build_context(
        agg,
        top,
        PulseDraft.model_validate(_load(run_dir, "pulse_draft.json")),
        [Quote.model_validate(quote) for quote in _load(run_dir, "quotes.json")["quotes"]],
        Taxonomy.model_validate(_load(run_dir, "taxonomy.json")),
    )


def _pulse_fields(ctx: PulseContext, markdown: str, run_dir: Path) -> dict[str, Any]:
    validation = _load(run_dir, "validation.json") if (run_dir / "validation.json").is_file() else None
    failed = {issue["code"] for issue in validation["issues"]} if validation else set(GATES)
    return {
        "title": ctx.title,
        "meta_line": ctx.meta_line,
        "themes": [
            {
                "name": theme.name,
                "share_pct": theme.share_pct,
                "trend": theme.trend.strip(" ()"),
                "summary": theme.summary,
            }
            for theme in ctx.themes
        ],
        "quotes": [
            {
                "text": quote.text,
                "rating": quote.rating,
                "source": quote.source,
                "month": quote.month,
                "theme_id": quote.theme_id,
            }
            for quote in ctx.quotes
        ],
        "actions": [{"text": action.action, "themes": action.theme_names} for action in ctx.actions],
        "word_count": count_words(markdown),
        "checks": {gate: gate not in failed for gate in GATES},
        "validated": bool(validation and validation["passed"]),
    }


def _load(run_dir: Path, name: str) -> Any:
    return json.loads((run_dir / name).read_text(encoding="utf-8"))
