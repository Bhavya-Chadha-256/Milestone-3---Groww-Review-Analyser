"""Publish gates V1–V7: nothing reaches Google unless every gate passes (architecture.md §3.9, Task 4.3).

Messages carry counts and field names only, never review text or matched personal data.
"""

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pulse.config import PulseSettings
from pulse.llm.quotes import verify_quote
from pulse.models import Classification, Review, Taxonomy
from pulse.privacy.scrub import PLACEHOLDER_RE, find_pii
from pulse.report.render import PulseContext
from pulse.report.words import count_words

MAX_THEMES = 5
NUMBER = re.compile(r"(?<![\w+])\d[\d,.]*%?(?!\w)")  # "30%", "1,200"; not "2FA" or "T+1" (REN-5)
QUOTE_ROUTED = frozenset({"V3", "V6"})


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str


@dataclass(frozen=True)
class ValidationReport:
    passed: bool
    issues: list[ValidationIssue] = field(default_factory=list)


def validate(  # craftsman-ignore: WARN-PY001, PY003 (plan interface; hint on closing line)
    ctx: PulseContext,
    markdown: str,
    top_ids: Sequence[str],
    taxonomy: Taxonomy,
    reviews_by_id: Mapping[str, Review],
    cls_by_id: Mapping[str, Classification],
    limits: PulseSettings,
) -> ValidationReport:
    issues = [
        *_counts(ctx, top_ids, limits),
        *_order(ctx, top_ids, taxonomy),
        *_quotes(ctx, reviews_by_id, cls_by_id, limits),
        *_action_themes(ctx, top_ids),
        *_lengths(ctx, markdown, limits),
        *_privacy(ctx, markdown),
        *_numbers(ctx),
    ]
    return ValidationReport(passed=not issues, issues=issues)


def route(issues: Sequence[ValidationIssue]) -> Literal["quotes", "compose"] | None:
    """Quote or privacy problems redo quotes and compose; anything else redoes compose with feedback."""
    if any(issue.code in QUOTE_ROUTED for issue in issues):
        return "quotes"
    return "compose" if issues else None


def _counts(ctx: PulseContext, top_ids: Sequence[str], limits: PulseSettings) -> list[ValidationIssue]:
    expected = {"quotes": limits.quotes, "actions": limits.actions, "theme summaries": len(top_ids)}
    actual = {
        "quotes": len(ctx.quotes),
        "actions": len(ctx.draft.actions),
        "theme summaries": len(ctx.draft.themes),
    }
    return [
        ValidationIssue("V1", f"{actual[name]} {name}; expected {expected[name]}")
        for name in expected
        if actual[name] != expected[name]
    ]


def _order(ctx: PulseContext, top_ids: Sequence[str], taxonomy: Taxonomy) -> list[ValidationIssue]:
    issues = []
    drafted = [summary.theme_id for summary in ctx.draft.themes]
    if drafted != list(top_ids):
        issues.append(
            ValidationIssue("V2", f"theme summaries are {drafted}; expected {list(top_ids)} in that order")
        )
    if len(taxonomy.themes) > MAX_THEMES:
        issues.append(
            ValidationIssue("V2", f"taxonomy has {len(taxonomy.themes)} themes; at most {MAX_THEMES}")
        )
    return issues


def _quotes(
    ctx: PulseContext,
    reviews_by_id: Mapping[str, Review],
    cls_by_id: Mapping[str, Classification],
    limits: PulseSettings,
) -> list[ValidationIssue]:
    issues = []
    for quote in ctx.quotes:
        review = reviews_by_id.get(quote.review_id)
        if review is None or not verify_quote(quote.text, review.text, limits.quote_max_words):
            issues.append(
                ValidationIssue("V3", f"quote for {quote.theme_id} is not a verbatim span of its review")
            )
        label = cls_by_id.get(quote.review_id)
        if label is None or label.theme_id != quote.theme_id:
            issues.append(
                ValidationIssue("V3", f"quote for {quote.theme_id} comes from a review of another theme")
            )
    quoted = Counter(quote.review_id for quote in ctx.quotes)
    repeated = [review_id for review_id, count in quoted.items() if count > 1]
    if repeated:
        issues.append(ValidationIssue("V3", f"{len(repeated)} review(s) quoted more than once"))
    return issues


def _action_themes(ctx: PulseContext, top_ids: Sequence[str]) -> list[ValidationIssue]:
    issues = []
    for index, action in enumerate(ctx.draft.actions, start=1):
        unknown = [theme_id for theme_id in action.theme_ids if theme_id not in top_ids]
        if not action.theme_ids or unknown:
            issues.append(
                ValidationIssue("V4", f"action {index} must name only top themes; got {action.theme_ids}")
            )
    return issues


def _lengths(ctx: PulseContext, markdown: str, limits: PulseSettings) -> list[ValidationIssue]:
    actions = list(enumerate(ctx.draft.actions, start=1))
    fields = [
        *(
            (f"summary for {summary.theme_id}", summary.summary, limits.summary_max_words)
            for summary in ctx.draft.themes
        ),
        *((f"action {index}", action.action, limits.action_max_words) for index, action in actions),
        *((f"rationale {index}", action.rationale, limits.rationale_max_words) for index, action in actions),
        *((f"quote for {quote.theme_id}", quote.text, limits.quote_max_words) for quote in ctx.quotes),
    ]
    issues = [
        ValidationIssue("V5", f"{name} has {words} words (max {limit})")
        for name, text, limit in fields
        if (words := count_words(text)) > limit
    ]
    total = count_words(markdown)
    if total > limits.max_words:
        issues.append(ValidationIssue("V5", f"pulse has {total} words (max {limits.max_words})"))
    return issues


def _privacy(ctx: PulseContext, markdown: str) -> list[ValidationIssue]:
    kinds = sorted({match.kind for match in find_pii(markdown)})
    issues = [ValidationIssue("V6", f"personal data in the pulse: {', '.join(kinds)}")] if kinds else []
    issues += [
        ValidationIssue("V6", f"quote for {quote.theme_id} contains a redaction placeholder")
        for quote in ctx.quotes
        if PLACEHOLDER_RE.search(quote.text)
    ]
    return issues


def _numbers(ctx: PulseContext) -> list[ValidationIssue]:
    texts = [(f"summary for {summary.theme_id}", summary.summary) for summary in ctx.draft.themes]
    texts += [(f"action {index}", action.action) for index, action in enumerate(ctx.draft.actions, start=1)]
    return [
        ValidationIssue("V7", f"{name} contains a number; statistics are added by the system")
        for name, text in texts
        if NUMBER.search(text)
    ]
