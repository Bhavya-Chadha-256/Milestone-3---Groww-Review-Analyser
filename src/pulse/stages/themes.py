"""The discover, classify and aggregate stages (architecture.md §3.3–3.5, Task 3.5).

Themes come from eligible reviews only; window totals count every stored review (D1). The LLM is
reached only through `context.llm`, and every count it produces is checked and ranked in code.
"""

from dataclasses import asdict

from pulse.analysis.aggregate import (
    Aggregates,
    InsufficientData,
    aggregate,
    rank_top,
    theme_mapping,
    trend_arrow,
)
from pulse.analysis.sample import stratified_sample
from pulse.llm.classify import (
    ClassifyResult,
    classify_reviews,
    estimate_classify_tokens,
    prompt_version,
    system_tokens,
)
from pulse.llm.discover import discover_taxonomy, needs_rediscovery
from pulse.llm.schema import DailyBudgetExhausted, LLMError
from pulse.models import Classification, Review, RunStatus, Taxonomy
from pulse.pipeline import RunContext, register_stage
from pulse.timeutil import window_bounds_utc

SAMPLE_SEED = 7  # evaluation §5 rule 4
RESUME_HINT = "run again after 00:00 UTC: pulse run --week {week} --from {stage} --until {until}"


@register_stage("discover")
def discover(context: RunContext) -> RunStatus | None:
    eligible = eligible_reviews(context)
    needed = context.settings.themes.min_reviews
    if len(eligible) < needed:  # ING-6: stop before any LLM call
        context.report.sections["Taxonomy"] = (
            f"Only {len(eligible)} eligible reviews in the window; need {needed}. "
            "Nothing was sent to the LLM."
        )
        return RunStatus.INSUFFICIENT_DATA
    taxonomy = context.store.latest_taxonomy()
    if taxonomy is None or context.opts.rediscover:
        try:
            taxonomy = _discover_new(context, eligible)
        except DailyBudgetExhausted:
            return _budget_failure(context, "discover", "daily_token_budget: discovery could not finish")
    _use_taxonomy(context, taxonomy)
    return None


@register_stage("classify")
def classify(context: RunContext) -> RunStatus:
    taxonomy = stage_taxonomy(context)
    eligible = eligible_reviews(context)
    result = _classify(context, eligible, taxonomy)
    if result.stopped_for_budget:
        done = len(eligible) - result.pending_after
        return _budget_failure(
            context, "classify", f"daily_token_budget: classified {done} of {len(eligible)}"
        )
    return RunStatus.CLASSIFIED


@register_stage("aggregate")
def aggregate_stage(context: RunContext) -> RunStatus:
    taxonomy = stage_taxonomy(context)
    labels = stage_labels(context, taxonomy)
    agg = _aggregate(context, labels, taxonomy)
    threshold = context.settings.themes.rediscover_unthemed_threshold
    unthemed_high = needs_rediscovery(agg.actionable_unthemed_share, threshold)
    if unthemed_high and not _created_in_run(context, taxonomy):
        rediscovered = _rediscover(context, taxonomy, labels)
        if isinstance(rediscovered, RunStatus):
            return rediscovered
        taxonomy, labels, agg = rediscovered
        unthemed_high = needs_rediscovery(agg.actionable_unthemed_share, threshold)
    if unthemed_high and context.report.counts.get("rediscovered"):
        context.report.warnings.append("unthemed_high_after_rediscovery")  # LLM-8: no second attempt
    context.save_artifact("aggregates.json", asdict(agg))
    context.report.warnings.extend(agg.warnings)
    context.report.sections["LLM usage"] = _usage_section(context)
    try:
        top = rank_top(agg, context.settings.themes.top_n)
    except InsufficientData as error:  # AGG-3
        context.report.sections["Top themes"] = str(error)
        return RunStatus.INSUFFICIENT_DATA
    context.state["top"] = top
    context.report.sections["Top themes"] = _top_section(agg, top)
    return RunStatus.AGGREGATED


def _rediscover(  # craftsman-ignore: WARN-PY001 (regex counts the return tuple)
    context: RunContext, old: Taxonomy, old_labels: dict[str, Classification]
) -> tuple[Taxonomy, dict[str, Classification], Aggregates] | RunStatus:
    """One rediscovery when the actionable unthemed share is high (D2, LLM-16), then reclassify."""
    eligible = eligible_reviews(context)
    try:
        taxonomy = _discover_new(context, eligible)
    except DailyBudgetExhausted:
        return _budget_failure(context, "discover", "daily_token_budget: rediscovery could not finish")
    context.report.counts["rediscovered"] = True  # persisted, so a resumed run never rediscovers twice
    _use_taxonomy(context, taxonomy)
    result = _classify(context, eligible, taxonomy)
    if result.stopped_for_budget:
        done = len(eligible) - result.pending_after
        return _budget_failure(
            context, "classify", f"daily_token_budget: reclassified {done} of {len(eligible)}"
        )
    mapping = theme_mapping(old_labels, result.classifications)
    lines = [f"- `{old_id}` → `{new_id}`" for old_id, new_id in mapping.items()] or ["- (no overlap)"]
    context.report.sections["Theme mapping"] = (
        f"Taxonomy v{old.version} → v{taxonomy.version} (most reviews of each old theme moved to):\n"
        + "\n".join(lines)
    )
    return taxonomy, result.classifications, _aggregate(context, result.classifications, taxonomy)


def _discover_new(context: RunContext, eligible: list[Review]) -> Taxonomy:
    settings, llm = context.settings, stage_llm(context)
    themes = settings.themes
    sample = stratified_sample(eligible, themes.discovery_sample, SAMPLE_SEED, settings.window.timezone)
    taxonomy = discover_taxonomy(
        llm,
        sample,
        themes.seed_hints,
        context.store.next_taxonomy_version(),
        context.run_id,
        settings=settings.llm,
        batch_size=themes.discovery_batch,
        min_themes=themes.min,
        max_themes=themes.max,
        name_max_words=themes.name_max_words,
    )
    context.store.save_taxonomy(taxonomy)
    return taxonomy


def _created_in_run(context: RunContext, taxonomy: Taxonomy) -> bool:
    """Read from the taxonomy itself, so the guard survives a `--from` resume (LLM-8)."""
    return taxonomy.created_from_run == context.run_id


def _use_taxonomy(context: RunContext, taxonomy: Taxonomy) -> None:
    context.state["taxonomy"] = taxonomy
    context.save_artifact("taxonomy.json", taxonomy)
    context.report.counts["taxonomy_version"] = taxonomy.version
    created = " (created in this run)" if _created_in_run(context, taxonomy) else ""
    lines = [f"- `{theme.id}` **{theme.name}**: {theme.definition}" for theme in taxonomy.themes]
    context.report.sections["Taxonomy"] = f"Version {taxonomy.version}{created}:\n" + "\n".join(lines)


def _classify(context: RunContext, eligible: list[Review], taxonomy: Taxonomy) -> ClassifyResult:
    llm, settings = stage_llm(context), context.settings.llm
    chunk_size = settings.classify_chunk_size
    result = classify_reviews(llm, context.store, eligible, taxonomy, chunk_size=chunk_size)
    context.state["classifications"] = result.classifications
    pending = len(eligible) - result.from_cache
    estimate = estimate_classify_tokens(
        pending, chunk_size, system_tokens(taxonomy), settings.stages["classify"].max_tokens
    )
    context.report.counts.update(
        classified=len(result.classifications),
        unclassified=len(result.unclassified),
        served_models=llm.usage.served_models(),
        tokens_total=llm.usage.total_tokens(),
        daily_tokens_left=llm.remaining_tokens_today(),
    )
    context.report.sections["Classification"] = "\n".join(
        [
            f"- Eligible reviews: {len(eligible)}; from cache: {result.from_cache}; sent: {result.sent}",
            f"- Estimated tokens for pending reviews: {estimate:,}; "
            f"left today: {llm.remaining_tokens_today():,}",
            f"- Unclassified after retries: {len(result.unclassified)}",
        ]
    )
    return result


def _aggregate(context: RunContext, labels: dict[str, Classification], taxonomy: Taxonomy) -> Aggregates:
    since, until = window_bounds_utc(context.window)
    settings = context.settings
    return aggregate(
        context.store.reviews_between(since, until),  # every review in the window (D1)
        labels,
        taxonomy,
        context.window,
        min_theme_reviews=settings.pulse.min_theme_reviews,
        trend_z=settings.themes.trend_z,
        broad_theme_warn=settings.monitoring.broad_theme_warn,
    )


def eligible_reviews(context: RunContext) -> list[Review]:
    if "eligible" not in context.state:
        since, until = window_bounds_utc(context.window)
        context.state["eligible"] = context.store.reviews_between(since, until, eligible_only=True)
    return context.state["eligible"]


def stage_taxonomy(context: RunContext) -> Taxonomy:
    if "taxonomy" not in context.state:  # resumed with --from: read what discover saved
        context.state["taxonomy"] = context.load_artifact("taxonomy.json", Taxonomy)
    return context.state["taxonomy"]


def stage_labels(context: RunContext, taxonomy: Taxonomy) -> dict[str, Classification]:
    if "classifications" not in context.state:
        ids = [review.review_id for review in eligible_reviews(context)]
        context.state["classifications"] = context.store.get_classifications(
            ids, taxonomy.version, prompt_version()
        )
    return context.state["classifications"]


def stage_llm(context: RunContext) -> object:
    if context.llm is None:
        raise LLMError("this stage needs an LLM; set GROQ_API_KEY in .env")
    return context.llm


def _budget_failure(context: RunContext, stage: str, message: str) -> RunStatus:
    hint = RESUME_HINT.format(week=context.window.iso_week, stage=stage, until=context.opts.until_stage)
    line = f"{message}; {hint}"  # LLM-13
    context.report.warnings.append(line)
    context.report.sections.setdefault("Classification", line)
    return RunStatus.FAILED


def _top_section(agg: Aggregates, top: list) -> str:
    basis = (
        f"From {agg.total} eligible reviews (8+ words, English/Hinglish) "
        f"of {agg.window_total} in the window. "
        f"Window average {agg.window_mean_rating or 0:.1f}★; eligible average {agg.mean_rating or 0:.1f}★."
    )
    rows = [
        "| # | Theme | Reviews | Share | Avg ★ | 1–2★ | Trend | Note |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for rank, stats in enumerate(top, start=1):
        trend = trend_arrow(stats.trend_pts, stats.trend_significant)
        trend_text = f"{trend} {stats.trend_pts:+d} pts" if stats.trend_pts is not None else "n/a"
        rows.append(
            f"| {rank} | {stats.name} | {stats.count} | {stats.share:.0%} | {stats.mean_rating or 0:.1f} "
            f"| {stats.low_rating_share:.0%} | {trend_text} | {'low sample' if stats.low_sample else ''} |"
        )
    unthemed = (
        f"Unthemed: {agg.unthemed_share:.0%} of eligible reviews "
        f"(actionable, negative or mixed only: {agg.actionable_unthemed_share:.0%})."
    )
    return "\n".join([basis, "", *rows, "", unthemed])


def _usage_section(context: RunContext) -> str:
    llm = context.llm
    if llm is None:
        return "No LLM calls."
    rows = ["| Stage | Requests | Input tokens | Output tokens |", "|---|---|---|---|"]
    for stage, totals in llm.usage.by_stage().items():
        rows.append(f"| {stage} | {totals['requests']} | {totals['input']:,} | {totals['output']:,} |")
    served = ", ".join(f"{model} × {count}" for model, count in llm.usage.served_models().items()) or "none"
    cost = llm.usage.cost(context.settings.llm.pricing)
    return "\n".join(
        [
            *rows,
            "",
            f"- Served models: {served}",
            f"- Tokens left today: {llm.remaining_tokens_today():,}",
            f"- Cost: ${cost:.2f}",
        ]
    )
