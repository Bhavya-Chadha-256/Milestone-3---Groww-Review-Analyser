"""The pulse as Markdown (Doc), plain text and HTML (email) from one context (architecture.md §3.8, Task 4.3).

Every number in the pulse comes from code (`Aggregates`, `ThemeStats`); the model only supplies words.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from pulse.analysis.aggregate import Aggregates, ThemeStats, trend_arrow
from pulse.llm.compose import PulseDraft
from pulse.models import Quote, Taxonomy
from pulse.report.words import escape_md
from pulse.timeutil import format_range

TEMPLATES = Path(__file__).parent / "templates"
SOURCE_NAMES = {"play_store": "Play Store", "app_store": "App Store"}
PRODUCT = "Groww"


@dataclass(frozen=True)
class ThemeLine:
    name: str
    share_pct: int
    trend: str
    summary: str


@dataclass(frozen=True)
class ActionLine:
    action: str
    theme_names: list[str]


@dataclass(frozen=True)
class PulseContext:
    title: str
    meta_line: str
    themes: list[ThemeLine]
    quotes: list[Quote]
    actions: list[ActionLine]
    draft: PulseDraft


def build_context(  # craftsman-ignore: WARN-PY001, PY003 (plan interface; hint on closing line)
    agg: Aggregates,
    top: Sequence[ThemeStats],
    draft: PulseDraft,
    quotes: Sequence[Quote],
    taxonomy: Taxonomy,
) -> PulseContext:
    monday = agg.window.latest_week_start
    names = {theme.id: theme.name for theme in taxonomy.themes}
    return PulseContext(
        title=f"{PRODUCT} Review Pulse — Week of {monday.day} {monday:%b %Y}",
        meta_line=_meta_line(agg),
        themes=_theme_lines(top, draft),
        quotes=list(quotes),
        actions=[
            ActionLine(action.action, [names.get(theme_id, theme_id) for theme_id in action.theme_ids])
            for action in draft.actions
        ],
        draft=draft,
    )


def _theme_lines(top: Sequence[ThemeStats], draft: PulseDraft) -> list[ThemeLine]:
    summaries = {summary.theme_id: summary.summary for summary in draft.themes}
    return [
        ThemeLine(
            name=stats.name,
            share_pct=round(stats.share * 100),
            trend=trend_clause(stats.trend_pts, stats.trend_significant),
            summary=summaries.get(stats.theme_id, ""),
        )
        for stats in top
    ]


def trend_clause(pts: int | None, significant: bool) -> str:
    arrow = trend_arrow(pts, significant)
    if not arrow:
        return ""
    if arrow == "→":
        return " (steady this week)"
    return f" ({arrow}{abs(pts)} pts this week)"


def render_markdown(ctx: PulseContext) -> str:
    return _render("pulse.md.j2", ctx, autoescape=False)


def render_text(ctx: PulseContext) -> str:
    """Plain text for the email body: no Markdown, quotes verbatim."""
    return _render("pulse.txt.j2", ctx, autoescape=False)


def render_html(ctx: PulseContext) -> str:
    """HTML for the email body; review text is escaped by Jinja's autoescape (REN-7)."""
    return _render("pulse.html.j2", ctx, autoescape=True)


def _meta_line(agg: Aggregates) -> str:
    sources = " + ".join(name for key, name in SOURCE_NAMES.items() if agg.sources.get(key))
    rating = f" · avg {agg.window_mean_rating:.1f}★" if agg.window_mean_rating is not None else ""
    return (
        f"{agg.window_total} reviews · {format_range(agg.window.start, agg.window.end)} · {sources}{rating}"
    )


def _render(template: str, ctx: PulseContext, *, autoescape: bool) -> str:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=autoescape,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
    )
    environment.filters["escape_md"] = escape_md
    environment.filters["source_name"] = lambda source: SOURCE_NAMES.get(source, source)
    return environment.get_template(template).render(ctx=ctx)
