"""Classification against the human gold set: E-CLS-1…7 (evaluation §4, Task 3.6).

Production `classify_reviews` runs twice against a temporary store, so cached labels can't answer.
"""

import tempfile
from collections import Counter
from pathlib import Path
from typing import Literal

from pulse.config import Settings
from pulse.evals.gold import GOLD_PATH, GoldSet, check_gold_reviews, load_gold
from pulse.evals.metrics import class_labels, cohen_kappa, macro_f1, majority_baseline, paired_bootstrap_ci
from pulse.evals.runner import RESULTS_ROOT, EvalRun
from pulse.llm.classify import ClassifyResult, classify_reviews
from pulse.llm.schema import LLMError
from pulse.models import UNTHEMED, Classification
from pulse.store import Store

REPETITIONS = 2


class EvalAborted(LLMError):
    """The suite could not finish for an infrastructure reason; nothing is scored (evaluation §5 rule 2)."""


def run_classification_suite(  # craftsman-ignore: WARN-PY001, PY003 (plan interface; hint on closing line)
    settings: Settings,
    llm: object,
    store: Store,
    dataset: Literal["gold", "drift"] = "gold",
    *,
    gold_path: Path | None = None,
    out_root: Path = RESULTS_ROOT,
) -> Path:
    taxonomy = store.latest_taxonomy()
    if taxonomy is None:
        raise RuntimeError("no taxonomy yet; run discovery first")
    gold = load_gold(gold_path or _dataset_path(dataset), taxonomy=taxonomy)
    classes = class_labels(taxonomy)
    if macro_f1(gold.labels, gold.labels, classes)[0] != 1.0:  # oracle check: the scorer itself must be sound
        raise RuntimeError("oracle check failed: gold labels scored against themselves did not give F1 = 1.0")
    reviews = store.get_reviews(list(gold.labels))
    check_gold_reviews(gold, reviews)
    results = [_classify_fresh(llm, settings, reviews, taxonomy) for _ in range(REPETITIONS)]
    runs = [result.classifications for result in results]
    run = EvalRun(f"classification-{dataset}", out_root)
    _record_cases(run, gold, runs, results[0].failures, llm, settings)
    _record_metrics(run, gold, runs, classes)
    return run.finish(llm.usage, settings.llm.pricing)


def _dataset_path(dataset: str) -> Path:
    if dataset == "gold":
        return GOLD_PATH
    drift = sorted(GOLD_PATH.parent.glob("drift_*.csv"))
    if not drift:
        raise FileNotFoundError(f"no drift_YYYY-MM.csv in {GOLD_PATH.parent}")
    return drift[-1]


def _classify_fresh(llm: object, settings: Settings, reviews: list, taxonomy: object) -> ClassifyResult:
    with tempfile.TemporaryDirectory() as scratch:
        result = classify_reviews(
            llm,
            Store(Path(scratch) / "eval.db"),
            reviews,
            taxonomy,
            chunk_size=settings.llm.classify_chunk_size,
        )
    if result.stopped_for_budget:  # §5 rule 2: a partial run would score unsent reviews as wrong
        raise EvalAborted(
            f"daily token budget ran out with {result.pending_after} of {len(reviews)} gold reviews unsent; "
            "nothing was scored. Run the suite again after 00:00 UTC, on a day with no backfill."
        )
    return result


def _record_cases(  # craftsman-ignore: WARN-PY001, PY003 (hint on closing line)
    run: EvalRun,
    gold: GoldSet,
    runs: list[dict[str, Classification]],
    failures: dict[str, str],
    llm: object,
    settings: Settings,
) -> None:
    served = Counter(llm.usage.served_models()).most_common(1)
    served_model = served[0][0] if served else settings.llm.model
    for review_id, label in gold.labels.items():
        first = runs[0].get(review_id)
        if first is None:
            run.error(review_id, failures.get(review_id, "unclassified"), "no label after retries")
            continue
        record = {
            "gold": label,
            "pred": first.theme_id,
            "pred_repeat": runs[1].get(review_id).theme_id if runs[1].get(review_id) else None,
            "gold_sentiment": gold.sentiments.get(review_id),
            "pred_sentiment": first.sentiment,
        }
        run.case(review_id, record, served_model=served_model, primary_model=settings.llm.model)


def _record_metrics(
    run: EvalRun, gold: GoldSet, runs: list[dict[str, Classification]], classes: list[str]
) -> None:
    headline = {case["case_id"] for case in run.headline_cases()}
    truth = {key: label for key, label in gold.labels.items() if key in headline}  # errors are never scored
    pred = {key: runs[0][key].theme_id for key in truth if key in runs[0]}
    score, per_class = macro_f1(truth, pred, classes)
    run.metric(
        "E-CLS-1", score, target=0.80, gate="blocking", ci=paired_bootstrap_ci(truth, pred, classes=classes)
    )
    run.metric(
        "E-CLS-2",
        min((values[2] for values in per_class.values()), default=None),
        target=0.65,
        gate="warning",
    )
    unthemed = per_class.get(UNTHEMED)
    run.metric("E-CLS-3", min(unthemed[0], unthemed[1]) if unthemed else None, target=0.80, gate="warning")
    run.metric("E-CLS-4", cohen_kappa(truth, pred), target=0.70, gate="tracking")
    run.metric("E-CLS-4-majority-f1", majority_baseline(truth, classes), gate="tracking")
    unclassified = sum(key not in runs[0] for key in gold.labels) / len(gold.labels)
    run.metric("E-CLS-5", unclassified, target=0.01, gate="warning", higher_is_better=False)
    with_sentiment = [key for key in gold.sentiments if key in runs[0]]
    sentiment_hits = sum(runs[0][key].sentiment == gold.sentiments[key] for key in with_sentiment)
    run.metric("E-CLS-6", sentiment_hits / len(with_sentiment) if with_sentiment else None, gate="tracking")
    both = [key for key in gold.labels if key in runs[0] and key in runs[1]]
    agreement = (
        sum(runs[0][key].theme_id == runs[1][key].theme_id for key in both) / len(both) if both else None
    )
    run.metric("E-CLS-7", agreement, target=0.95, gate="tracking")
