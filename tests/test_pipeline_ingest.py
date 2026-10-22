import csv
import json
import os
import shutil
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from factories import make_classification

from pulse.config import Settings
from pulse.ingest.base import ImportReport
from pulse.ingest.file_import import FileImportSource
from pulse.models import RawReview, Review, RunStatus
from pulse.pipeline import (
    STAGE_FUNCS,
    MissingArtifact,
    RunContext,
    RunInProgress,
    RunOptions,
    StageNotAvailable,
    WeekNotComplete,
    run_lock,
    run_pipeline,
)
from pulse.store import Store
from pulse.timeutil import window_for_week

REPO = Path(__file__).resolve().parents[1]
RAW_FIXTURES = REPO / "tests" / "fixtures" / "raw"
REDTEAM_CSV = REPO / "evals" / "redteam" / "pii_reviews.csv"
TODAY = date(2026, 10, 19)
STARTED = datetime(2026, 10, 19, 5, 0, tzinfo=UTC)


class FailingSource:
    name = "boom"

    def __init__(self) -> None:
        self.report = ImportReport()

    def fetch(self, since: datetime, until: datetime) -> Iterator[RawReview]:
        raise RuntimeError("source exploded")


def file_source(settings: Settings, raw_dir: Path) -> FileImportSource:
    return FileImportSource(raw_dir, Path(settings.paths.mappings))


def ingest_opts(**overrides: object) -> RunOptions:
    fields: dict[str, object] = {"today": TODAY, "until_stage": "ingest", "started_at": STARTED}
    return RunOptions(**(fields | overrides))


def all_reviews(store: Store) -> list[Review]:
    return store.reviews_between(datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC))


def test_ingest_stage_writes_manifest_and_report(tmp_settings: Settings, tmp_store: Store) -> None:
    out = run_pipeline(
        tmp_settings, ingest_opts(), store=tmp_store, sources=[file_source(tmp_settings, RAW_FIXTURES)]
    )
    assert out.status == RunStatus.INGESTED
    assert out.run_id == "2026-W42-20261019T050000"
    manifest = json.loads((out.run_dir / "manifest.json").read_text())
    assert manifest["window"]["iso_week"] == "2026-W42"
    assert manifest["counts"]["imported"] == 10 and manifest["config_hash"]
    report = (out.run_dir / "report.md").read_text()
    assert "invalid_rating" in report and "## Import" in report and "## Window" in report
    run = tmp_store.latest_run("2026-W42")
    assert run is not None and run["status"] == "INGESTED" and run["summary"]["imported"] == 10


def test_report_never_contains_review_text(tmp_settings: Settings, tmp_store: Store) -> None:  # PII-10
    out = run_pipeline(
        tmp_settings, ingest_opts(), store=tmp_store, sources=[file_source(tmp_settings, RAW_FIXTURES)]
    )
    written = (out.run_dir / "report.md").read_text() + (out.run_dir / "manifest.json").read_text()
    for content in ("Withdrawal stuck", "Charts are slow", "KYC took", "[pan]"):
        assert content not in written


def test_ingest_stores_all_reviews_and_flags_exclusions(
    tmp_settings: Settings, tmp_store: Store
) -> None:  # ING-24
    out = run_pipeline(
        tmp_settings, ingest_opts(), store=tmp_store, sources=[file_source(tmp_settings, RAW_FIXTURES)]
    )
    stored = all_reviews(tmp_store)
    assert len(stored) == 10
    excluded = [review for review in stored if review.excluded_reason]
    assert {review.excluded_reason for review in excluded} == {"too_short"}
    eligible = tmp_store.reviews_between(
        datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC), eligible_only=True
    )
    assert len(eligible) == len(stored) - len(excluded) > 0
    report = (out.run_dir / "report.md").read_text()
    assert f"too_short: {len(excluded)}" in report and f"Eligible for themes: {len(eligible)}" in report


def test_ingest_reflags_stored_review_that_now_fails_filter(  # craftsman-ignore: PY003 (hint on closing line)
    tmp_settings: Settings, tmp_store: Store, tmp_path: Path
) -> None:  # ING-26
    raw_dir = tmp_path / "exports"
    raw_dir.mkdir()
    export = raw_dir / "app_store_feed__x.csv"
    header = "native_id,rating,title,text,posted_at,app_version\n"
    export.write_text(
        header + "e1,2,,The order page freezes every time I try to sell,2026-10-10T08:00:00+00:00,\n"
    )
    run_pipeline(tmp_settings, ingest_opts(), store=tmp_store, sources=[file_source(tmp_settings, raw_dir)])
    review = all_reviews(tmp_store)[0]
    assert review.excluded_reason is None
    tmp_store.save_classifications([make_classification(review.review_id, "trading")])
    export.write_text(header + "e1,2,,Order page freezes,2026-10-10T08:00:00+00:00,\n")
    later = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)
    run_pipeline(
        tmp_settings,
        ingest_opts(started_at=later),
        store=tmp_store,
        sources=[file_source(tmp_settings, raw_dir)],
    )
    assert all_reviews(tmp_store)[0].excluded_reason == "too_short"
    assert tmp_store.get_classifications([review.review_id], 1, "classify-v1") == {}


def test_source_failure_is_isolated(tmp_settings: Settings, tmp_store: Store) -> None:  # ING-17
    out = run_pipeline(
        tmp_settings,
        ingest_opts(),
        store=tmp_store,
        sources=[FailingSource(), file_source(tmp_settings, RAW_FIXTURES)],
    )
    assert out.status == RunStatus.INGESTED
    assert "source_failed:boom" in (out.run_dir / "report.md").read_text()
    alone = run_pipeline(
        tmp_settings,
        ingest_opts(started_at=datetime(2026, 10, 19, 6, 0, tzinfo=UTC)),
        store=tmp_store,
        sources=[FailingSource()],
    )
    assert alone.status == RunStatus.FAILED
    assert tmp_store.latest_run("2026-W42")["status"] == "FAILED"


def test_failed_source_leaves_raw_files_unpurged(tmp_settings: Settings, tmp_store: Store) -> None:
    raw_dir = Path(tmp_settings.paths.raw)
    raw_dir.mkdir(parents=True)
    old = raw_dir / "app_store_feed__old.csv"
    shutil.copy(RAW_FIXTURES / "app_store_feed__sample.csv", old)
    stamp = datetime(2026, 9, 1, tzinfo=UTC).timestamp()
    os.utime(old, (stamp, stamp))
    run_pipeline(
        tmp_settings,
        ingest_opts(),
        store=tmp_store,
        sources=[FailingSource(), file_source(tmp_settings, raw_dir)],
    )
    assert old.exists()
    run_pipeline(
        tmp_settings,
        ingest_opts(started_at=datetime(2026, 10, 19, 6, 0, tzinfo=UTC)),
        store=tmp_store,
        sources=[file_source(tmp_settings, raw_dir)],
    )
    assert not old.exists() and len(all_reviews(tmp_store)) == 3


def test_concurrent_run_same_week_refused(tmp_settings: Settings, tmp_store: Store) -> None:  # DEL-11
    with run_lock(Path(tmp_settings.paths.runs), "2026-W42"), pytest.raises(RunInProgress):
        run_pipeline(tmp_settings, ingest_opts(), store=tmp_store, sources=[])


def test_run_refuses_incomplete_week(tmp_settings: Settings, tmp_store: Store) -> None:  # OPS-2
    with pytest.raises(WeekNotComplete):
        run_pipeline(
            tmp_settings, ingest_opts(today=date(2026, 10, 21), week="2026-W43"), store=tmp_store, sources=[]
        )


def test_load_artifact_missing_errors_clearly(  # craftsman-ignore: PY003 (hint on closing line)
    tmp_settings: Settings, tmp_store: Store, tmp_path: Path
) -> None:  # OPS-1
    context = RunContext(
        settings=tmp_settings,
        opts=ingest_opts(from_stage="compose"),
        store=tmp_store,
        sources=[],
        run_id="2026-W42-x",
        run_dir=tmp_path,
        window=window_for_week("2026-W42", 12, "Asia/Kolkata"),
    )
    context.stage = "compose"
    with pytest.raises(MissingArtifact, match="cannot resume from compose: missing quotes.json"):
        context.load_artifact("quotes.json")
    context.save_artifact("quotes.json", {"quotes": []})
    assert context.load_artifact("quotes.json") == {"quotes": []}


def test_resume_without_earlier_run_errors(tmp_settings: Settings, tmp_store: Store) -> None:  # OPS-1
    with pytest.raises(MissingArtifact, match="cannot resume"):
        run_pipeline(tmp_settings, ingest_opts(from_stage="ingest"), store=tmp_store, sources=[])


def test_until_unregistered_stage_raises(  # craftsman-ignore: PY003 (hint on closing line)
    monkeypatch: pytest.MonkeyPatch, tmp_settings: Settings, tmp_store: Store
) -> None:
    monkeypatch.delitem(STAGE_FUNCS, "discover", raising=False)
    with pytest.raises(StageNotAvailable):
        run_pipeline(tmp_settings, ingest_opts(until_stage="discover"), store=tmp_store, sources=[])


def test_pii_redteam_recall(tmp_settings: Settings, tmp_store: Store, tmp_path: Path) -> None:  # E-PII-3
    shutil.copy(REDTEAM_CSV, tmp_path / "generic__pii_reviews.csv")
    run_pipeline(tmp_settings, ingest_opts(), store=tmp_store, sources=[file_source(tmp_settings, tmp_path)])
    with REDTEAM_CSV.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    stored = all_reviews(tmp_store)
    assert len(stored) == len(rows) == 25 and all(review.excluded_reason is None for review in stored)
    blob = "".join(review.model_dump_json() for review in stored)
    for row in rows:
        for value in filter(None, row["seeded_pii"].split("|")):
            assert value not in blob, row["id"]
        if row["negative"] == "true":
            assert row["text"] in blob, row["id"]


def test_unread_raw_files_are_never_purged(tmp_settings: Settings, tmp_store: Store) -> None:  # PII-11
    raw_dir = Path(tmp_settings.paths.raw)
    raw_dir.mkdir(parents=True)
    read = raw_dir / "app_store_feed__old.csv"
    shutil.copy(RAW_FIXTURES / "app_store_feed__sample.csv", read)
    unread = [raw_dir / "notes.txt", raw_dir / "generic__wrong_columns.csv"]
    unread[0].write_text("keep me")
    unread[1].write_text("id,text\n1,hello there\n")
    stamp = datetime(2026, 9, 1, tzinfo=UTC).timestamp()
    for path in (read, *unread):
        os.utime(path, (stamp, stamp))
    run_pipeline(tmp_settings, ingest_opts(), store=tmp_store, sources=[file_source(tmp_settings, raw_dir)])
    assert not read.exists() and all(path.exists() for path in unread)


def test_nothing_is_purged_without_a_file_source(tmp_settings: Settings, tmp_store: Store) -> None:
    raw_dir = Path(tmp_settings.paths.raw)
    raw_dir.mkdir(parents=True)
    old = raw_dir / "app_store_feed__old.csv"
    shutil.copy(RAW_FIXTURES / "app_store_feed__sample.csv", old)
    stamp = datetime(2026, 9, 1, tzinfo=UTC).timestamp()
    os.utime(old, (stamp, stamp))
    run_pipeline(tmp_settings, ingest_opts(), store=tmp_store, sources=[])
    assert old.exists()
