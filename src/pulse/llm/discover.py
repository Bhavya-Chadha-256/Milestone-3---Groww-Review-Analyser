"""Theme discovery: candidate themes per review batch, then one merge into 3–5 themes (architecture.md §3.3).

Batches are filled by token estimate, so every request fits the provider's tokens-per-minute limit (LLM-12).
"""

import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import BaseModel

from pulse.config import LLMSettings
from pulse.llm.prompts import load_prompt, reviews_json
from pulse.llm.schema import LLMRequest, estimate_tokens, wire_schema
from pulse.models import UNTHEMED, Review, Taxonomy, Theme

THEME_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
TPM_HEADROOM = 0.9  # keep each request below 90% of tokens-per-minute

Rules = tuple[int, int, int]  # (min_themes, max_themes, name_max_words)


class DiscoveredTheme(BaseModel):
    id: str
    name: str
    definition: str
    include_hints: list[str]
    exclude_hints: list[str]
    example_keys: list[int]


class DiscoveryOutput(BaseModel):
    themes: list[DiscoveredTheme]


class TaxonomyError(Exception):
    pass


def needs_rediscovery(actionable_unthemed_share: float, threshold: float) -> bool:
    return actionable_unthemed_share > threshold


def discover_taxonomy(  # craftsman-ignore: WARN-PY001, PY003 (plan interface; hint on closing line)
    llm: object,
    sample: Sequence[Review],
    seed_hints: Sequence[str],
    version: int,
    run_id: str,
    *,
    settings: LLMSettings,
    batch_size: int = 100,
    min_themes: int = 3,
    max_themes: int = 5,
    name_max_words: int = 4,
) -> Taxonomy:
    hints = f"Seed hints (suggestions only): {', '.join(seed_hints)}." if seed_hints else "Seed hints: none."
    pass_system = load_prompt("discover").text
    batches = _batches(sample, pass_system, hints, settings, batch_size)
    final = _discover(llm, batches, hints, pass_system, (min_themes, max_themes, name_max_words))
    return _to_taxonomy(final, version, run_id, sample)


def _discover(  # craftsman-ignore: WARN-PY001, PY003 (hint on closing line)
    llm: object, batches: list[list[Review]], hints: str, pass_system: str, rules: Rules
) -> DiscoveryOutput:
    """One validated pass if the sample fits one request; else a pass per batch, then a merge."""
    if len(batches) == 1:
        return _ask_validated(llm, pass_system, _pass_user(hints, batches[0], 1, 1), rules)
    candidates = []
    for index, batch in enumerate(batches, start=1):
        user = _pass_user(hints, batch, index, len(batches))
        output = llm.structured(LLMRequest("discover", pass_system, user, DiscoveryOutput))
        candidates.append((output, reviews_json(batch)[1]))
    return _ask_validated(llm, load_prompt("discover_merge").text, _merge_user(hints, candidates), rules)


def _to_taxonomy(final: DiscoveryOutput, version: int, run_id: str, sample: Sequence[Review]) -> Taxonomy:
    themes = [
        Theme(
            id=item.id,
            name=item.name,
            definition=item.definition,
            include_hints=item.include_hints,
            exclude_hints=item.exclude_hints,
        )
        for item in final.themes
    ]
    return Taxonomy(
        version=version,
        themes=themes,
        created_from_run=run_id,
        created_at=datetime.now(UTC),
        sample_review_ids=[review.review_id for review in sample],
    )


def _pass_user(hints: str, batch: Sequence[Review], index: int, total: int) -> str:
    payload, _ = reviews_json(batch)
    return f"{hints}\nBatch {index} of {total}: propose themes for these reviews.\n{payload}"


def _merge_user(hints: str, candidates: list[tuple[DiscoveryOutput, dict[int, str]]]) -> str:
    rows = [
        {
            "batch": batch_number,
            "id": item.id,
            "name": item.name,
            "definition": item.definition,
            "include_hints": item.include_hints,
            "exclude_hints": item.exclude_hints,
            "supporting_reviews": sum(key in keys for key in item.example_keys),  # unknown keys dropped
        }
        for batch_number, (output, keys) in enumerate(candidates, start=1)
        for item in output.themes
    ]
    return (
        f"{hints}\nCandidate themes from {len(candidates)} batches:\n{json.dumps(rows, ensure_ascii=False)}"
    )


def _batches(  # craftsman-ignore: WARN-PY001, PY003 (hint on closing line)
    sample: Sequence[Review], system: str, hints: str, settings: LLMSettings, batch_size: int
) -> list[list[Review]]:
    """Greedy batches whose request (prompt + schema + output allowance) stays under the per-minute limit."""
    overhead = (
        estimate_tokens(system)
        + estimate_tokens(hints)
        + estimate_tokens(json.dumps(wire_schema(DiscoveryOutput)))
        + settings.stages["discover"].max_tokens
        + 50  # framing text and the <reviews> wrapper
    )
    budget = int(settings.limits.tpm * TPM_HEADROOM) - overhead
    batches: list[list[Review]] = [[]]
    used = 0
    for review in sample:
        cost = estimate_tokens(reviews_json([review])[0]) + 2
        if batches[-1] and (used + cost > budget or len(batches[-1]) >= batch_size):
            batches.append([])
            used = 0
        batches[-1].append(review)
        used += cost
    return batches


def _ask_validated(llm: object, system: str, user: str, rules: Rules) -> DiscoveryOutput:
    output = llm.structured(LLMRequest("discover", system, user, DiscoveryOutput))
    problems = _problems(output, rules)
    if not problems:
        return output
    min_themes, max_themes, name_max_words = rules
    retry = (
        f"{user}\n\nReturn at least {min_themes} and at most {max_themes} themes, each named in at most "
        f"{name_max_words} words. Problems with your previous answer: {'; '.join(problems)}"
    )
    output = llm.structured(LLMRequest("discover", system, retry, DiscoveryOutput))
    problems = _problems(output, rules)
    if problems:
        raise TaxonomyError("; ".join(problems))
    return output


def _problems(output: DiscoveryOutput, rules: Rules) -> list[str]:
    min_themes, max_themes, name_max_words = rules
    problems = []
    count = len(output.themes)
    if not min_themes <= count <= max_themes:
        problems.append(f"{count} themes returned; need {min_themes} to {max_themes}")  # LLM-6
    ids = [item.id for item in output.themes]
    problems += [
        f"duplicate theme id '{slug}'" for slug in sorted({slug for slug in ids if ids.count(slug) > 1})
    ]
    for item in output.themes:
        problems += _theme_problems(item, name_max_words)
    return problems


def _theme_problems(item: DiscoveredTheme, name_max_words: int) -> list[str]:
    problems = []
    if item.id == UNTHEMED:
        problems.append(f"'{UNTHEMED}' is reserved and cannot be a theme")
    elif not THEME_ID_RE.match(item.id):
        problems.append(f"theme id '{item.id}' must match {THEME_ID_RE.pattern}")
    words = len(item.name.split())
    if words > name_max_words:
        problems.append(f"theme '{item.id}' name has {words} words")  # REN-1
    return problems
