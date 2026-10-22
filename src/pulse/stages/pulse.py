"""The quotes, compose and validate stages, with routed retries (architecture.md §3.6–3.9, Task 4.4).

Validation decides what re-runs: quote or privacy problems redo quotes and compose; anything else redoes
compose with the gate messages as feedback, up to `pulse.retries` times.
"""

from dataclasses import asdict

from pulse.analysis.aggregate import Aggregates, ThemeStats, rank_top
from pulse.llm.compose import PulseDraft, compose_pulse, evidence_sample
from pulse.llm.quotes import QuoteUnavailable, candidate_pool, select_quotes
from pulse.models import Quote, RunStatus
from pulse.pipeline import RunContext, register_stage
from pulse.report.render import build_context, render_markdown
from pulse.report.validate import ValidationReport, route, validate
from pulse.stages.themes import eligible_reviews, stage_labels, stage_llm, stage_taxonomy


@register_stage("quotes")
def quotes_stage(context: RunContext) -> RunStatus:
    try:
        _select_quotes(context, attempt=0)
    except QuoteUnavailable as error:
        return _quote_failure(context, error)
    return RunStatus.QUOTED


@register_stage("compose")
def compose_stage(context: RunContext) -> RunStatus:
    _compose(context, feedback=None)
    return RunStatus.COMPOSED


@register_stage("validate")
def validate_stage(context: RunContext) -> RunStatus:
    limits = context.settings.pulse
    retries = 0
    report = _check(context)
    while not report.passed and retries < limits.retries:
        retries += 1
        context.report.counts["validation_retries"] = retries
        if not _redo(context, report, attempt=retries):
            return RunStatus.VALIDATION_FAILED
        report = _check(context)
    context.report.counts["validation_retries"] = retries
    context.report.sections["Validation"] = _validation_section(report, retries)
    return RunStatus.VALIDATED if report.passed else RunStatus.VALIDATION_FAILED


def _redo(context: RunContext, report: ValidationReport, attempt: int) -> bool:
    """Re-runs what `route` names; False when a theme no longer has a quotable review."""
    if route(report.issues) == "quotes":
        try:
            _select_quotes(context, attempt=attempt)  # a new seed reorders the candidate pools
        except QuoteUnavailable as error:
            _quote_failure(context, error)
            return False
    _compose(context, feedback=[f"{issue.code}: {issue.message}" for issue in report.issues])
    return True


def _select_quotes(context: RunContext, attempt: int) -> list[Quote]:
    limits, window = context.settings.pulse, context.window
    taxonomy = stage_taxonomy(context)
    reviews, labels, top = eligible_reviews(context), stage_labels(context, taxonomy), _top(context)
    quotes, fallbacks = select_quotes(stage_llm(context), top, reviews, labels, window, limits, seed=attempt)
    pools = {
        stats.theme_id: [
            review.review_id
            for review in candidate_pool(stats.theme_id, reviews, labels, window, limits, attempt)
        ]
        for stats in top
    }
    context.state["quotes"] = quotes
    context.save_artifact(
        "quotes.json",
        {"quotes": [quote.model_dump() for quote in quotes], "pools": pools, "fallbacks": fallbacks},
    )
    context.report.counts["quote_fallbacks"] = fallbacks  # M-RUN-3
    context.report.sections["Quotes"] = f"- First-sentence fallbacks used: {fallbacks} of {len(quotes)}"
    return quotes


def _compose(context: RunContext, feedback: list[str] | None) -> PulseDraft:
    taxonomy, top = stage_taxonomy(context), _top(context)
    reviews, labels = eligible_reviews(context), stage_labels(context, taxonomy)
    evidence = {
        stats.theme_id: evidence_sample(
            stats.theme_id, reviews, labels, context.settings.pulse.evidence_per_theme
        )
        for stats in top
    }
    draft = compose_pulse(
        stage_llm(context), top, _aggregates(context), _quotes(context), evidence, taxonomy, feedback=feedback
    )
    context.state["draft"] = draft
    context.save_artifact("pulse_draft.json", draft)
    return draft


def _check(context: RunContext) -> ValidationReport:
    taxonomy, top = stage_taxonomy(context), _top(context)
    ctx = build_context(_aggregates(context), top, _draft(context), _quotes(context), taxonomy)
    markdown = render_markdown(ctx)
    reviews = {review.review_id: review for review in eligible_reviews(context)}
    report = validate(
        ctx,
        markdown,
        [stats.theme_id for stats in top],
        taxonomy,
        reviews,
        stage_labels(context, taxonomy),
        context.settings.pulse,
    )
    (context.run_dir / "pulse.md").write_text(markdown, encoding="utf-8")
    context.save_artifact("validation.json", asdict(report))
    context.state["pulse_markdown"] = markdown
    context.report.sections["Pulse"] = (  # report.md shows validated pulse text only (CLAUDE.md privacy)
        markdown if report.passed else "Not shown: validation failed. The draft is in pulse.md."
    )
    return report


def _aggregates(context: RunContext) -> Aggregates:
    if "aggregates" not in context.state:  # resumed with --from: rebuild from what aggregate saved
        data = context.load_artifact("aggregates.json")
        themes = [ThemeStats(**stats) for stats in data.pop("themes")]
        data.pop("window")
        context.state["aggregates"] = Aggregates(window=context.window, themes=themes, **data)
    return context.state["aggregates"]


def _top(context: RunContext) -> list[ThemeStats]:
    if "top" not in context.state:
        context.state["top"] = rank_top(_aggregates(context), context.settings.themes.top_n)
    return context.state["top"]


def _quotes(context: RunContext) -> list[Quote]:
    if "quotes" not in context.state:
        data = context.load_artifact("quotes.json")  # MissingArtifact names the file (OPS-1)
        context.state["quotes"] = [Quote.model_validate(quote) for quote in data["quotes"]]
    return context.state["quotes"]


def _draft(context: RunContext) -> PulseDraft:
    if "draft" not in context.state:
        context.state["draft"] = context.load_artifact("pulse_draft.json", PulseDraft)
    return context.state["draft"]


def _quote_failure(context: RunContext, error: QuoteUnavailable) -> RunStatus:
    context.report.sections["Quotes"] = f"{error}; nothing can be published this week (QUO-3)."
    context.report.warnings.append(f"quote_unavailable:{error.theme_id}")
    return RunStatus.VALIDATION_FAILED


def _validation_section(report: ValidationReport, retries: int) -> str:
    if report.passed:
        return f"All gates V1–V7 passed after {retries} retries."
    lines = [f"- {issue.code}: {issue.message}" for issue in report.issues]
    return f"Gates failed after {retries} retries:\n" + "\n".join(lines)
