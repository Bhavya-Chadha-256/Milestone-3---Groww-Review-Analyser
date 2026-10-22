import csv
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from factories import make_classification, make_review
from fakes import FakeLLM
from pydantic import BaseModel

from pulse.config import Pricing, Settings
from pulse.evals.gold import GOLD_COLUMNS, GoldMetaError, export_gold, load_gold
from pulse.evals.metrics import (
    class_labels,
    cohen_kappa,
    macro_f1,
    majority_baseline,
    paired_bootstrap_ci,
)
from pulse.evals.runner import EvalRun, compare
from pulse.evals.suites.classification import EvalAborted, run_classification_suite
from pulse.llm.schema import LLMRequest, LLMTruncated, UsageLedger
from pulse.models import Taxonomy, Theme
from pulse.store import Store
from pulse.timeutil import window_for_week

LATEST = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
CLASSES = ["support", "charges", "bugs", "unthemed"]


def taxonomy(sample_ids: list[str] | None = None, version: int = 1) -> Taxonomy:
    themes = [
        Theme(id=slug, name=slug.title(), definition=slug, include_hints=[], exclude_hints=[])
        for slug in CLASSES[:3]
    ]
    return Taxonomy(
        version=version,
        themes=themes,
        created_from_run="r",
        created_at=LATEST,
        sample_review_ids=sample_ids or [],
    )


def test_macro_f1_includes_unthemed_class() -> None:
    gold = {"a": "support", "b": "support", "c": "unthemed", "d": "charges"}
    pred = {"a": "support", "b": "unthemed", "c": "unthemed", "d": "charges"}
    score, per_class = macro_f1(gold, pred, CLASSES)
    assert set(per_class) == {"support", "charges", "unthemed"}
    precision, recall, f1, support = per_class["unthemed"]
    assert (precision, recall, support) == (0.5, 1.0, 1) and f1 == pytest.approx(2 / 3)
    assert score == pytest.approx((2 / 3 + 1.0 + 2 / 3) / 3)
    assert class_labels(taxonomy()) == CLASSES


def test_missing_prediction_counts_as_wrong() -> None:
    score, _ = macro_f1({"a": "support", "b": "charges"}, {"a": "support"}, CLASSES)
    assert score < 1.0


def test_cohen_kappa_and_majority_baseline_on_toy_data() -> None:
    first = {"a": "x", "b": "x", "c": "y", "d": "y"}
    assert cohen_kappa(first, first) == pytest.approx(1.0)
    assert cohen_kappa(first, {"a": "x", "b": "y", "c": "x", "d": "y"}) == pytest.approx(0.0)
    gold = {"a": "support", "b": "support", "c": "support", "d": "charges"}
    assert majority_baseline(gold, CLASSES) == pytest.approx((2 * 0.75 / 1.75 + 0) / 2)


def test_paired_bootstrap_is_seeded_and_brackets_point_estimate() -> None:
    gold = {f"r{index}": CLASSES[index % 4] for index in range(80)}
    pred = {key: (label if index % 5 else "unthemed") for index, (key, label) in enumerate(gold.items())}
    point, _ = macro_f1(gold, pred, CLASSES)
    low, high = paired_bootstrap_ci(gold, pred, classes=CLASSES, n=300, seed=0)
    assert low <= point <= high and (low, high) == paired_bootstrap_ci(
        gold, pred, classes=CLASSES, n=300, seed=0
    )
    diff_low, diff_high = paired_bootstrap_ci(gold, pred, pred, classes=CLASSES, n=300, seed=0)
    assert diff_low == diff_high == 0.0


def test_oracle_scores_one() -> None:
    gold = {f"r{index}": CLASSES[index % 4] for index in range(20)}
    assert macro_f1(gold, gold, CLASSES)[0] == 1.0


def seed_store(store: Store, count: int = 120) -> list[str]:
    reviews = [
        make_review(
            review_id=f"g{index:04d}",
            rating=(index % 5) + 1,
            source="app_store" if index % 10 == 0 else "play_store",
            posted_at=LATEST - timedelta(days=index % 60),
        )
        for index in range(count)
    ]
    reviews.append(make_review(review_id="dup0001", is_duplicate_text=True))
    reviews.append(make_review(review_id="short01", excluded_reason="too_short"))
    store.upsert_reviews(reviews)
    store.save_classifications(
        [
            make_classification(review.review_id, "bugs" if index < 20 else "support")
            for index, review in enumerate(reviews)
        ]
    )
    return [review.review_id for review in reviews]


def test_export_gold_excludes_discovery_sample_and_meets_class_minimum(
    tmp_store: Store, tmp_path: Path
) -> None:
    ids = seed_store(tmp_store)
    path = tmp_path / "gold_v1.csv"
    count = export_gold(
        tmp_store,
        window_for_week("2026-W42", 12, "Asia/Kolkata"),
        taxonomy(ids[20:50]),
        n=60,
        min_per_class=15,
        seed=7,
        path=path,
    )
    rows = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    chosen = {row["review_id"] for row in rows}
    assert count == len(rows) == 60
    assert not chosen & set(ids[20:50]) and "dup0001" not in chosen and "short01" not in chosen
    provisional_bugs = {f"g{index:04d}" for index in range(20)}
    assert len(chosen & provisional_bugs) >= 15
    meta = json.loads(path.with_suffix(".meta.json").read_text())
    assert meta["taxonomy_version"] == 1 and meta["kappa"] is None and "provisional" in meta["selection_note"]


def test_export_gold_has_no_author_or_model_label_columns(tmp_store: Store, tmp_path: Path) -> None:
    seed_store(tmp_store)
    path = tmp_path / "gold_v1.csv"
    export_gold(tmp_store, window_for_week("2026-W42", 12, "Asia/Kolkata"), taxonomy(), n=20, path=path)
    header = path.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert tuple(header) == GOLD_COLUMNS
    rows = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    assert all(row["gold_theme"] == "" and row["gold_sentiment"] == "" for row in rows)


def write_gold(path: Path, labels: dict[str, str], *, kappa: float | None, version: int = 1) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=GOLD_COLUMNS)
        writer.writeheader()
        for review_id, theme in labels.items():
            writer.writerow(
                {
                    "review_id": review_id,
                    "rating": 2,
                    "source": "play_store",
                    "title": "",
                    "text": "t",
                    "gold_theme": theme,
                    "gold_sentiment": "negative",
                }
            )
    meta = {
        "taxonomy_version": version,
        "annotators": 2,
        "kappa": kappa,
        "adjudicated": 3,
        "selection_note": "test",
    }
    path.with_suffix(".meta.json").write_text(json.dumps(meta))
    return path


def test_load_gold_refuses_low_kappa_or_mismatched_taxonomy(tmp_path: Path) -> None:
    labels = {"g0001": "support", "g0002": "unthemed"}
    with pytest.raises(GoldMetaError, match="kappa"):
        load_gold(write_gold(tmp_path / "low.csv", labels, kappa=0.6), current_version=1)
    with pytest.raises(GoldMetaError, match="kappa"):
        load_gold(write_gold(tmp_path / "none.csv", labels, kappa=None), current_version=1)
    with pytest.raises(GoldMetaError, match="taxonomy"):
        load_gold(write_gold(tmp_path / "old.csv", labels, kappa=0.8, version=1), current_version=2)
    gold = load_gold(write_gold(tmp_path / "ok.csv", labels, kappa=0.8), current_version=1)
    assert gold.labels == labels and gold.sentiments == {"g0001": "negative", "g0002": "negative"}


def test_runner_separates_errors_and_unexpected_model_rows(tmp_path: Path) -> None:
    run = EvalRun("classification", tmp_path, today="2026-10-22")
    run.case(
        "a",
        {"gold": "support", "pred": "support"},
        served_model="openai/gpt-oss-120b",
        primary_model="openai/gpt-oss-120b",
    )
    run.case(
        "b", {"gold": "support", "pred": "bugs"}, served_model="other", primary_model="openai/gpt-oss-120b"
    )
    run.error("c", "unclassified", "no label after retries")
    assert [case["case_id"] for case in run.headline_cases()] == ["a"]
    run.metric("E-CLS-1", 1.0, target=0.8, gate="blocking", ci=(0.9, 1.0))
    out = run.finish(UsageLedger(), Pricing())
    summary = json.loads((out / "summary.json").read_text())
    assert (summary["scored"], summary["errors"], summary["unexpected_model"]) == (2, 1, 1)
    assert (
        summary["metrics"]["E-CLS-1"]["pass"] is True and (out / "errors.jsonl").read_text().count("\n") == 1
    )
    assert "E-CLS-1" in (out / "report.md").read_text()


def finished_run(root: Path, name: str, preds: dict[str, str], gold: dict[str, str]) -> Path:
    run = EvalRun("classification", root / name, today="2026-10-22")
    for key, label in gold.items():
        run.case(key, {"gold": label, "pred": preds[key]}, served_model="m", primary_model="m")
    run.metric("E-CLS-1", macro_f1(gold, preds, CLASSES)[0], target=0.8, gate="blocking")
    return run.finish(UsageLedger(), Pricing())


def test_compare_marks_within_noise(tmp_path: Path) -> None:
    gold = {f"r{index:03d}": CLASSES[index % 4] for index in range(100)}
    first = {key: (label if index % 7 else "unthemed") for index, (key, label) in enumerate(gold.items())}
    second = {key: (label if index % 8 else "unthemed") for index, (key, label) in enumerate(gold.items())}
    rows = compare(finished_run(tmp_path, "a", first, gold), finished_run(tmp_path, "b", second, gold))
    assert [(row.metric_id, row.verdict) for row in rows] == [("E-CLS-1", "within noise")]
    bad = {key: "unthemed" for key in gold}
    worse = compare(finished_run(tmp_path, "c", first, gold), finished_run(tmp_path, "d", bad, gold))
    assert worse[0].verdict == "regressed"


def echo_labels(theme_for: Callable[[int], str]) -> Callable:
    def handle(req: LLMRequest) -> BaseModel:
        payload = req.user[req.user.index("<reviews>") + len("<reviews>") : req.user.index("</reviews>")]
        items = [
            {"k": item["k"], "theme_id": theme_for(item["k"]), "sentiment": "negative"}
            for item in json.loads(payload)
        ]
        return req.schema.model_validate({"items": items})

    return handle


def test_classification_suite_uses_temp_store(
    tmp_settings: Settings, tmp_store: Store, tmp_path: Path
) -> None:
    ids = seed_store(tmp_store, 40)
    tmp_store.save_taxonomy(taxonomy())
    gold_labels = {review_id: "support" for review_id in ids[:30]}
    gold_path = write_gold(tmp_path / "gold_v1.csv", gold_labels, kappa=0.85)
    tmp_store.save_classifications(
        [make_classification(review_id, "charges") for review_id in gold_labels]
    )  # stale cache
    llm = FakeLLM({"classify": echo_labels(lambda key: "support")})
    out = run_classification_suite(
        tmp_settings, llm, tmp_store, "gold", gold_path=gold_path, out_root=tmp_path / "results"
    )
    summary = json.loads((out / "summary.json").read_text())
    assert summary["metrics"]["E-CLS-1"]["value"] == 1.0 and summary["metrics"]["E-CLS-7"]["value"] == 1.0
    assert (
        sum(
            len(json.loads(call.user[call.user.index("<reviews>") + 9 : call.user.index("</reviews>")]))
            for call in llm.calls
        )
        == 60
    )
    assert {"E-CLS-1", "E-CLS-2", "E-CLS-3", "E-CLS-4", "E-CLS-5", "E-CLS-6", "E-CLS-7"} <= set(
        summary["metrics"]
    )


def gold_fixture(store: Store, tmp_path: Path) -> Path:
    ids = seed_store(store, 40)
    store.save_taxonomy(taxonomy())
    return write_gold(tmp_path / "gold_v1.csv", dict.fromkeys(ids[:30], "support"), kappa=0.85)


def test_suite_aborts_when_daily_budget_runs_out(
    tmp_settings: Settings, tmp_store: Store, tmp_path: Path
) -> None:  # evaluation §5 rule 2, review I3
    gold_path = gold_fixture(tmp_store, tmp_path)
    llm = FakeLLM({"classify": echo_labels(lambda key: "support")}, tokens_left=1500, tokens_per_call=1000)
    with pytest.raises(EvalAborted, match="00:00 UTC"):
        run_classification_suite(
            tmp_settings, llm, tmp_store, "gold", gold_path=gold_path, out_root=tmp_path / "results"
        )
    assert not (tmp_path / "results").exists()


def test_infrastructure_failures_are_not_scored(
    tmp_settings: Settings, tmp_store: Store, tmp_path: Path
) -> None:  # evaluation §5 rule 2, review I3
    gold_path = gold_fixture(tmp_store, tmp_path)

    def truncate_one_star(req: LLMRequest) -> BaseModel:
        payload = req.user[req.user.index("<reviews>") + len("<reviews>") : req.user.index("</reviews>")]
        if any(item["rating"] == 1 for item in json.loads(payload)):
            raise LLMTruncated("response hit max_completion_tokens")
        return echo_labels(lambda key: "support")(req)

    llm = FakeLLM({"classify": truncate_one_star})
    out = run_classification_suite(
        tmp_settings, llm, tmp_store, "gold", gold_path=gold_path, out_root=tmp_path / "results"
    )
    summary = json.loads((out / "summary.json").read_text())
    errors = [json.loads(line) for line in (out / "errors.jsonl").read_text().splitlines()]
    assert summary["metrics"]["E-CLS-1"]["value"] == 1.0 and summary["metrics"]["E-CLS-5"]["value"] == 0.2
    assert len(errors) == 6 and {error["failure_class"] for error in errors} == {"truncated"}
