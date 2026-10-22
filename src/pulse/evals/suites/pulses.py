"""The paid pulses suite: the live pipeline on the fixture weeks, stopping before delivery (Task 4.5).

`normal` and `burst` must produce a validated pulse; `thin` must end INSUFFICIENT_DATA. Records E-PUL-1,
E-PUL-2, E-QUO-1 and E-ACT-5. Each dataset runs in its own temporary store and runs directory.
"""

import json
import shutil
import tempfile
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pulse.config import Settings
from pulse.evals.runner import RESULTS_ROOT, EvalRun
from pulse.ingest.file_import import FileImportSource
from pulse.llm.quotes import verify_quote
from pulse.models import RunStatus
from pulse.pipeline import RunOptions, run_pipeline
from pulse.store import Store

WEEKS_DIR = Path("tests/fixtures/weeks")
DATASETS = ("normal", "burst", "thin")
PULSE_DATASETS = ("normal", "burst")
WEEK = "2026-W42"
TODAY = date(2026, 10, 19)
STARTED = datetime(2026, 10, 19, 5, tzinfo=UTC)
EXPECTED = {"normal": RunStatus.VALIDATED, "burst": RunStatus.VALIDATED, "thin": RunStatus.INSUFFICIENT_DATA}


def run_pulses_suite(
    settings: Settings, llm: object, *, datasets_dir: Path = WEEKS_DIR, out_root: Path = RESULTS_ROOT
) -> Path:
    results = {name: _run_dataset(settings, llm, Path(datasets_dir) / name) for name in DATASETS}
    run = EvalRun("pulses", out_root)
    served = Counter(llm.usage.served_models()).most_common(1)
    for name, result in results.items():
        model = served[0][0] if served else settings.llm.model
        run.case(name, result, served_model=model, primary_model=settings.llm.model)
    _record_metrics(run, results)
    return run.finish(llm.usage, settings.llm.pricing)


def _run_dataset(settings: Settings, llm: object, dataset_dir: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        paths = settings.paths.model_copy(
            update={"db": root / "pulse.db", "runs": root / "runs", "raw": root / "raw"}
        )
        local = settings.model_copy(update={"paths": paths}, deep=True)
        Path(paths.raw).mkdir()
        shutil.copy(dataset_dir / "generic__data.csv", Path(paths.raw) / "generic__data.csv")
        store = Store(Path(paths.db))
        outcome = run_pipeline(
            local,
            RunOptions(today=TODAY, week=WEEK, until_stage="validate", started_at=STARTED),
            store=store,
            sources=[FileImportSource(Path(paths.raw), Path(paths.mappings))],
            llm=llm,
        )
        return _result(outcome.status, outcome.run_dir, store, settings.pulse.quote_max_words)


def _result(status: RunStatus, run_dir: Path, store: Store, max_words: int) -> dict[str, Any]:
    """Counts and IDs only: no review text leaves the temporary run."""
    summary = store.latest_run(WEEK)["summary"]
    result: dict[str, Any] = {
        "status": str(status),
        "validation_retries": summary.get("validation_retries"),
        "quote_fallbacks": summary.get("quote_fallbacks"),
        "quotes": 0,
        "quotes_verified": 0,
        "action_coverage": None,
        "duplicate_share": None,
    }
    if (run_dir / "aggregates.json").is_file():
        result["duplicate_share"] = _read(run_dir / "aggregates.json")["duplicate_share"]
    if (run_dir / "quotes.json").is_file():
        result["quotes"], result["quotes_verified"] = _quote_counts(
            _read(run_dir / "quotes.json"), store, max_words
        )
    if (run_dir / "pulse_draft.json").is_file():
        result["action_coverage"] = _action_coverage(_read(run_dir / "pulse_draft.json"))
    return result


def _quote_counts(data: dict[str, Any], store: Store, max_words: int) -> tuple[int, int]:
    quotes = data["quotes"]
    reviews = {
        review.review_id: review for review in store.get_reviews([quote["review_id"] for quote in quotes])
    }
    verified = sum(
        quote["review_id"] in reviews
        and verify_quote(quote["text"], reviews[quote["review_id"]].text, max_words)
        for quote in quotes
    )
    return len(quotes), verified


def _action_coverage(draft: dict[str, Any]) -> int:
    """How many of the top themes the actions address together (E-ACT-5 needs at least 2)."""
    top_ids = {theme["theme_id"] for theme in draft["themes"]}
    return len({theme_id for action in draft["actions"] for theme_id in action["theme_ids"]} & top_ids)


def _record_metrics(run: EvalRun, results: dict[str, dict[str, Any]]) -> None:
    gate_pass = sum(results[name]["status"] == str(expected) for name, expected in EXPECTED.items())
    run.metric("E-PUL-1", gate_pass / len(EXPECTED), target=1.0, gate="blocking")
    first_try = sum(
        results[name]["status"] == str(RunStatus.VALIDATED) and results[name]["validation_retries"] == 0
        for name in PULSE_DATASETS
    )
    run.metric("E-PUL-2", first_try / len(PULSE_DATASETS), target=0.80, gate="warning")
    quotes = sum(result["quotes"] for result in results.values())
    verified = sum(result["quotes_verified"] for result in results.values())
    run.metric("E-QUO-1", verified / quotes if quotes else None, target=1.0, gate="blocking")
    drafted = [
        results[name]["action_coverage"]
        for name in PULSE_DATASETS
        if results[name]["action_coverage"] is not None
    ]
    covered = sum(count >= 2 for count in drafted)
    run.metric("E-ACT-5", covered / len(drafted) if drafted else None, target=1.0, gate="warning")
    run.metric("burst-duplicate-share", results["burst"]["duplicate_share"], target=0.10, gate="tracking")


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
