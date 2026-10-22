"""The data snapshot behind the hosted, read-only dashboard (Task 7.5, deployment.md).

The laptop exports it whenever new labels or pulses arrive; it is committed to this repo (the user's
choice, 2026-10-22) and Render unpacks it at startup, because a free Render service keeps no files across
restarts. It holds only what the dashboard reads: the database, gzip-compressed (scrubbed review text,
labels, taxonomies, runs, deliveries), and each week's newest pulse artifacts. Never `data/raw/`.
"""

import gzip
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pulse.config import Settings
from pulse.store import Store
from pulse.web.pulse_view import latest_with_pulse

DATABASE = "pulse.db.gz"
RUN_FILES = (
    "aggregates.json",
    "taxonomy.json",
    "quotes.json",
    "pulse_draft.json",
    "validation.json",
    "pulse.md",
    "report.json",  # warning codes and counts only
)


@dataclass(frozen=True)
class SnapshotReport:
    reviews: int
    classifications: int
    runs: list[str]
    size_bytes: int


def export_snapshot(settings: Settings, out_dir: Path) -> SnapshotReport:
    """Write `<out_dir>/pulse.db.gz` and `<out_dir>/runs/<run_id>/…` for each week's newest pulse."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    database = Path(settings.paths.db)
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "pulse.db"
        source = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
        target = sqlite3.connect(copy)
        source.backup(target)
        source.close()
        target.execute("VACUUM")
        reviews = target.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
        labels = target.execute("SELECT COUNT(*) FROM classifications").fetchone()[0]
        target.close()
        _gzip(copy, out_dir / DATABASE)
    runs = _copy_pulse_runs(settings, out_dir / "runs")
    size = sum(path.stat().st_size for path in out_dir.rglob("*") if path.is_file())
    return SnapshotReport(reviews=reviews, classifications=labels, runs=runs, size_bytes=size)


def unpack_snapshot(snapshot_dir: Path, work_dir: Path) -> Path:
    """The snapshot's database, decompressed into `work_dir`; returns its path."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    database = work_dir / "pulse.db"
    with gzip.open(Path(snapshot_dir) / DATABASE, "rb") as packed, database.open("wb") as unpacked:
        shutil.copyfileobj(packed, unpacked)
    return database


def _gzip(source: Path, target: Path) -> None:
    """Gzip with a fixed header time, so an unchanged database gives byte-identical output (no git churn)."""
    with source.open("rb") as raw, target.open("wb") as out:
        with gzip.GzipFile(filename="pulse.db", mode="wb", fileobj=out, compresslevel=9, mtime=0) as packed:
            shutil.copyfileobj(raw, packed)


def _copy_pulse_runs(settings: Settings, runs_out: Path) -> list[str]:
    if runs_out.exists():
        shutil.rmtree(runs_out)  # only the current pulses; older run folders are dropped
    store = Store.open_readonly(Path(settings.paths.db))
    weeks = [week for (week,) in store.conn.execute("SELECT DISTINCT iso_week FROM runs ORDER BY iso_week")]
    copied = []
    for week in weeks:
        run = latest_with_pulse(store, settings, week)
        if run is None:
            continue
        _copy_run_files(Path(settings.paths.runs) / run["run_id"], runs_out / run["run_id"])
        copied.append(run["run_id"])
    store.conn.close()
    return copied


def _copy_run_files(source: Path, target: Path) -> None:
    target.mkdir(parents=True)
    for name in RUN_FILES:
        if (source / name).is_file():
            shutil.copy2(source / name, target / name)
