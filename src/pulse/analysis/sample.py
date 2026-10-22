"""Stratified discovery sample (architecture.md §3.3, Task 3.2)."""

import random
from collections import defaultdict
from collections.abc import Iterable

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
    rng = random.Random(seed)
    queues = []
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda review: review.review_id)
        rng.shuffle(members)
        queues.append(members)
    sample: list[Review] = []
    while len(sample) < n and any(queues):
        for queue in queues:
            if queue and len(sample) < n:
                sample.append(queue.pop())
    return sample
