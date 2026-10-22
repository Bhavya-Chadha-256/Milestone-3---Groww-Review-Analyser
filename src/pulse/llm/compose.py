"""Theme summaries and action ideas for the pulse (architecture.md §3.7, Task 4.2).

The draft is structural only: every rule (counts, order, word limits, no numbers) is enforced by the
validator in Task 4.3, whose messages come back here as `feedback`.
"""

import json
import random
from collections.abc import Mapping, Sequence

from pydantic import BaseModel

from pulse.analysis.aggregate import Aggregates, ThemeStats
from pulse.llm.prompts import load_prompt, reviews_json
from pulse.llm.schema import LLMRequest
from pulse.models import Classification, Quote, Review, Taxonomy
from pulse.timeutil import format_range


class ThemeSummary(BaseModel):
    theme_id: str
    summary: str


class ActionIdea(BaseModel):
    theme_ids: list[str]
    action: str
    rationale: str


class PulseDraft(BaseModel):
    themes: list[ThemeSummary]
    actions: list[ActionIdea]


def evidence_sample(  # craftsman-ignore: WARN-PY001, PY003 (plan interface; hint on closing line)
    theme_id: str,
    reviews: Sequence[Review],
    cls_by_id: Mapping[str, Classification],
    k: int,
    seed: int = 0,
) -> list[Review]:
    """A seeded sample of up to `k` reviews classified in one theme."""
    members = sorted(
        (
            review
            for review in reviews
            if review.review_id in cls_by_id and cls_by_id[review.review_id].theme_id == theme_id
        ),
        key=lambda review: review.review_id,
    )
    return random.Random(seed).sample(members, min(k, len(members)))


def compose_pulse(  # craftsman-ignore: WARN-PY001, PY003 (plan interface; hint on closing line)
    llm: object,
    top: Sequence[ThemeStats],
    agg: Aggregates,
    quotes: Sequence[Quote],
    evidence: Mapping[str, Sequence[Review]],
    taxonomy: Taxonomy,
    feedback: Sequence[str] | None = None,
) -> PulseDraft:
    user = _user_prompt(top, agg, quotes, evidence, taxonomy)
    if feedback:
        user += "\n\nFix these problems from your previous draft:\n" + "\n".join(
            f"- {line}" for line in feedback
        )
    return llm.structured(LLMRequest("compose", load_prompt("compose").text, user, PulseDraft))


def _user_prompt(  # craftsman-ignore: WARN-PY001, PY003 (hint on closing line)
    top: Sequence[ThemeStats],
    agg: Aggregates,
    quotes: Sequence[Quote],
    evidence: Mapping[str, Sequence[Review]],
    taxonomy: Taxonomy,
) -> str:
    themes = _theme_rows(top, taxonomy)
    quote_rows = [{"theme_id": quote.theme_id, "quote": quote.text} for quote in quotes]
    blocks = [
        f"Evidence for {stats.theme_id}:\n{reviews_json(evidence.get(stats.theme_id, []))[0]}"
        for stats in top
    ]
    return "\n\n".join(
        [
            f"Reviews from {format_range(agg.window.start, agg.window.end)}.",
            f"Top themes, in order:\n{json.dumps(themes, ensure_ascii=False)}",
            f"Quotes:\n{json.dumps(quote_rows, ensure_ascii=False)}",
            *blocks,
        ]
    )


def _theme_rows(top: Sequence[ThemeStats], taxonomy: Taxonomy) -> list[dict]:
    definitions = {theme.id: theme.definition for theme in taxonomy.themes}
    return [
        {
            "theme_id": stats.theme_id,
            "name": stats.name,
            "definition": definitions.get(stats.theme_id, ""),
            "reviews": stats.count,  # context only: the pulse text must not contain numbers
            "low_rating_share": round(stats.low_rating_share, 2),
        }
        for stats in top
    ]
