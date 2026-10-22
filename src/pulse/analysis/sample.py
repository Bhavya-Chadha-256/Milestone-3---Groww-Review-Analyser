"""Stratified discovery sample (architecture.md §3.3, Task 3.2)."""

import random
from collections import defaultdict
from collections.abc import Hashable, Iterable, Mapping

from pulse.models import Review
from pulse.timeutil import iso_week_of


def stratified_sample(reviews: Iterable[Review], n: int, seed: int, timezone: str) -> list[Review]:
    """Up to `n` eligible, non-duplicate reviews, balanced across rating and IST week.

    Source is deliberately not a stratum (D6, LLM-15): App Store reviews are ~2% of eligible ones, and
    balancing by source would draw all of them, leaving none for the gold set.
    """
    groups: dict[tuple[int, str], list[Review]] = defaultdict(list)
    for review in reviews:
        if review.excluded_reason is None and not review.is_duplicate_text:
            groups[(review.rating, iso_week_of(review.posted_at, timezone))].append(review)
    return round_robin(groups, n, random.Random(seed))


def round_robin(groups: Mapping[Hashable, list[Review]], count: int, rng: random.Random) -> list[Review]:
    """Up to `count` reviews, one per group per round in key order; groups are seeded-shuffled by ID."""
    queues = [_shuffled(groups[key], rng) for key in sorted(groups)]
    drawn: list[Review] = []
    while len(drawn) < count and any(queues):
        drawn += [queue.pop() for queue in queues if queue][: count - len(drawn)]
    return drawn


def _shuffled(members: list[Review], rng: random.Random) -> list[Review]:
    ordered = sorted(members, key=lambda review: review.review_id)
    rng.shuffle(ordered)
    return ordered
