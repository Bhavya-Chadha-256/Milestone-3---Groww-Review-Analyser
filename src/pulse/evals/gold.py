"""Gold-set export and loading, following evaluation §3.1.

The CSV holds scrubbed review text for human annotators and is git-ignored. Model labels may be used to
pick reviews (class top-up) but are never written to the file.
"""

import csv
import json
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from pulse.analysis.sample import round_robin
from pulse.evals.metrics import class_labels
from pulse.llm.classify import prompt_version
from pulse.models import Review, Taxonomy
from pulse.store import Store
from pulse.timeutil import Window, window_bounds_utc

GOLD_COLUMNS = ("review_id", "rating", "source", "title", "text", "gold_theme", "gold_sentiment")
GOLD_PATH = Path("evals/gold/gold_v1.csv")
MIN_KAPPA = 0.70


class GoldMetaError(Exception):
    pass


@dataclass
class GoldMeta:
    taxonomy_version: int
    annotators: int | None
    kappa: float | None
    adjudicated: int | None
    selection_note: str


@dataclass
class GoldSet:
    labels: dict[str, str]
    sentiments: dict[str, str]
    meta: GoldMeta


def meta_path(path: Path) -> Path:
    return Path(path).with_suffix(".meta.json")


def export_gold(  # craftsman-ignore: WARN-PY001, PY003 (plan interface; hint on closing line)
    store: Store,
    window: Window,
    taxonomy: Taxonomy,
    *,
    n: int = 200,
    min_per_class: int = 15,
    seed: int = 7,
    path: Path = GOLD_PATH,
) -> int:
    """Write `n` unlabelled reviews for annotation, plus the meta file; returns the number of rows."""
    pool = _pool(store, window, taxonomy)
    provisional = store.get_classifications(
        [review.review_id for review in pool], taxonomy.version, prompt_version()
    )
    rng = random.Random(seed)
    chosen = _top_up(pool, provisional, class_labels(taxonomy), min_per_class, n, rng)
    chosen += _stratified_fill(pool, {review.review_id for review in chosen}, n - len(chosen), rng)
    rows = sorted(chosen, key=lambda review: review.review_id)
    _write_csv(Path(path), rows)
    note = (
        f"{len(rows)} eligible, non-duplicate reviews outside the discovery sample; "
        f"at most {min_per_class} per class picked first using provisional model labels "
        f"(taxonomy v{taxonomy.version}, labels not exported), "
        "then stratified by rating and source. Rare themes are slightly over-represented."
    )
    meta = GoldMeta(taxonomy.version, annotators=None, kappa=None, adjudicated=None, selection_note=note)
    meta_path(path).write_text(json.dumps(asdict(meta), indent=2) + "\n", encoding="utf-8")
    return len(rows)


def load_gold(path: Path, *, current_version: int) -> GoldSet:
    """The labelled gold set; refuses one without agreement κ ≥ 0.70 or from another taxonomy version."""
    meta = GoldMeta(**json.loads(meta_path(path).read_text(encoding="utf-8")))
    if meta.kappa is None or meta.kappa < MIN_KAPPA:
        raise GoldMetaError(f"gold kappa is {meta.kappa}; annotator agreement must be at least {MIN_KAPPA}")
    if meta.taxonomy_version != current_version:
        raise GoldMetaError(
            f"gold set is for taxonomy v{meta.taxonomy_version}, current is v{current_version}; "
            "remap with theme_mapping and have a human verify it"
        )
    with Path(path).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    unlabelled = [row["review_id"] for row in rows if not row["gold_theme"].strip()]
    if unlabelled:
        raise GoldMetaError(f"{len(unlabelled)} gold rows have no gold_theme")
    labels = {row["review_id"]: row["gold_theme"].strip() for row in rows}
    sentiments = {
        row["review_id"]: row["gold_sentiment"].strip() for row in rows if row["gold_sentiment"].strip()
    }
    return GoldSet(labels=labels, sentiments=sentiments, meta=meta)


def _pool(store: Store, window: Window, taxonomy: Taxonomy) -> list[Review]:
    """Eligible window reviews that discovery never saw, without duplicates (§3.1 step 2)."""
    since, until = window_bounds_utc(window)
    seen = set(taxonomy.sample_review_ids)
    reviews = store.reviews_between(since, until, eligible_only=True)
    return [review for review in reviews if review.review_id not in seen and not review.is_duplicate_text]


def _top_up(  # craftsman-ignore: WARN-PY001, PY003 (hint on closing line)
    pool: Sequence[Review],
    provisional: dict,
    classes: Sequence[str],
    per_class: int,
    limit: int,
    rng: random.Random,
) -> list[Review]:
    by_class: dict[str, list[Review]] = defaultdict(list)
    for review in pool:
        if review.review_id in provisional:
            by_class[provisional[review.review_id].theme_id].append(review)
    chosen: list[Review] = []
    for name in classes:
        members = sorted(by_class.get(name, []), key=lambda review: review.review_id)
        rng.shuffle(members)
        chosen += members[: max(0, min(per_class, limit - len(chosen)))]
    return chosen


def _stratified_fill(pool: Sequence[Review], taken: set[str], count: int, rng: random.Random) -> list[Review]:
    groups: dict[tuple[int, str], list[Review]] = defaultdict(list)
    for review in pool:
        if review.review_id not in taken:
            groups[(review.rating, review.source)].append(review)
    return round_robin(groups, count, rng)


def _write_csv(path: Path, reviews: Sequence[Review]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=GOLD_COLUMNS)
        writer.writeheader()
        for review in reviews:
            writer.writerow(
                {
                    "review_id": review.review_id,
                    "rating": review.rating,
                    "source": review.source,
                    "title": review.title or "",
                    "text": review.text,
                    "gold_theme": "",
                    "gold_sentiment": "",
                }
            )
