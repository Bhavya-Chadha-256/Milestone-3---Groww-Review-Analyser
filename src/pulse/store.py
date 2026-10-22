"""SQLite persistence for reviews, classifications, taxonomies, runs and deliveries (architecture.md §6).

Each table keeps its key columns indexed and the full model as a JSON `data` column.
"""

import json
import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pulse.models import Classification, Delivery, Review, RunStatus, Taxonomy
from pulse.timeutil import Window

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    review_id TEXT PRIMARY KEY, posted_at TEXT NOT NULL, text_hash TEXT NOT NULL, eligible INTEGER NOT NULL,
    data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS reviews_posted_at ON reviews (posted_at);
CREATE TABLE IF NOT EXISTS classifications (
    review_id TEXT NOT NULL, taxonomy_version INTEGER NOT NULL, prompt_version TEXT NOT NULL,
    data TEXT NOT NULL, PRIMARY KEY (review_id, taxonomy_version, prompt_version));
CREATE TABLE IF NOT EXISTS taxonomies (version INTEGER PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, iso_week TEXT NOT NULL, status TEXT, created_at TEXT NOT NULL,
    window TEXT NOT NULL, summary TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS runs_iso_week ON runs (iso_week);
CREATE TABLE IF NOT EXISTS deliveries (iso_week TEXT PRIMARY KEY, data TEXT NOT NULL);
"""

ID_CHUNK = 500  # stays well under SQLite's bound-parameter limit


@dataclass(frozen=True)
class UpsertResult:
    new: int
    updated: int


def _timestamp_key(value: datetime) -> str:
    """Fixed-width UTC text, so string order matches time order."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")


def _chunks(items: list[str]) -> Iterator[list[str]]:
    for start in range(0, len(items), ID_CHUNK):
        yield items[start : start + ID_CHUNK]


def _placeholders(count: int) -> str:
    return ",".join("?" * count)


class Store:
    def __init__(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)

    # Reviews

    def upsert_reviews(self, reviews: Iterable[Review]) -> UpsertResult:
        """Insert new reviews and replace changed ones; an edited text drops its classifications (ING-5)."""
        outcomes = []
        with self.conn:
            outcomes = [self._upsert_one(review) for review in reviews]
        return UpsertResult(new=outcomes.count("new"), updated=outcomes.count("updated"))

    def _upsert_one(self, review: Review) -> str:
        """Write one review; returns "new", "updated" or "unchanged"."""
        data = review.model_dump_json()
        row = self.conn.execute(
            "SELECT text_hash, data FROM reviews WHERE review_id = ?", (review.review_id,)
        ).fetchone()
        if row is not None and row[1] == data:
            return "unchanged"
        if row is not None and row[0] != review.text_hash:
            self.conn.execute("DELETE FROM classifications WHERE review_id = ?", (review.review_id,))
        self.conn.execute(
            "INSERT OR REPLACE INTO reviews (review_id, posted_at, text_hash, eligible, data) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                review.review_id,
                _timestamp_key(review.posted_at),
                review.text_hash,
                review.excluded_reason is None,
                data,
            ),
        )
        return "new" if row is None else "updated"

    def reviews_between(  # craftsman-ignore: PY003 (hint on closing line)
        self, start_utc: datetime, end_utc: datetime, *, eligible_only: bool = False
    ) -> list[Review]:
        """Reviews posted in [start_utc, end_utc), oldest first; `eligible_only` skips excluded ones."""
        rows = self.conn.execute(
            "SELECT data FROM reviews WHERE posted_at >= ? AND posted_at < ? AND eligible >= ? "
            "ORDER BY posted_at, review_id",
            (_timestamp_key(start_utc), _timestamp_key(end_utc), int(eligible_only)),
        )
        return [Review.model_validate_json(data) for (data,) in rows]

    # Taxonomies

    def save_taxonomy(self, taxonomy: Taxonomy) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO taxonomies (version, data) VALUES (?, ?)",
                (taxonomy.version, taxonomy.model_dump_json()),
            )

    def latest_taxonomy(self) -> Taxonomy | None:
        row = self.conn.execute("SELECT data FROM taxonomies ORDER BY version DESC LIMIT 1").fetchone()
        return Taxonomy.model_validate_json(row[0]) if row else None

    def next_taxonomy_version(self) -> int:
        (latest,) = self.conn.execute("SELECT MAX(version) FROM taxonomies").fetchone()
        return (latest or 0) + 1

    # Classifications

    def get_classifications(  # craftsman-ignore: PY003, WARN-PY001 (plan interface; hint on closing line)
        self, review_ids: Iterable[str], taxonomy_version: int, prompt_version: str
    ) -> dict[str, Classification]:
        found: dict[str, Classification] = {}
        for chunk in _chunks(list(review_ids)):
            rows = self.conn.execute(
                "SELECT data FROM classifications WHERE taxonomy_version = ? AND prompt_version = ? "
                f"AND review_id IN ({_placeholders(len(chunk))})",
                (taxonomy_version, prompt_version, *chunk),
            )
            for (data,) in rows:
                item = Classification.model_validate_json(data)
                found[item.review_id] = item
        return found

    def save_classifications(self, items: Iterable[Classification]) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO classifications (review_id, taxonomy_version, prompt_version, data) "
                "VALUES (?, ?, ?, ?)",
                [
                    (item.review_id, item.taxonomy_version, item.prompt_version, item.model_dump_json())
                    for item in items
                ],
            )

    # Runs

    def create_run(self, run_id: str, window: Window) -> None:
        window_json = json.dumps(asdict(window), default=str)
        with self.conn:
            self.conn.execute(
                "INSERT INTO runs (run_id, iso_week, status, created_at, window, summary) "
                "VALUES (?, ?, NULL, ?, ?, ?)",
                (run_id, window.iso_week, _timestamp_key(datetime.now(UTC)), window_json, "{}"),
            )

    def set_run_status(self, run_id: str, status: RunStatus, **fields: Any) -> None:
        """Set the status and merge `fields` into the run's JSON summary."""
        (summary,) = self.conn.execute("SELECT summary FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        merged = json.loads(summary) | fields
        with self.conn:
            self.conn.execute(
                "UPDATE runs SET status = ?, summary = ? WHERE run_id = ?",
                (str(status), json.dumps(merged, default=str), run_id),
            )

    def latest_run(self, iso_week: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT run_id, iso_week, status, created_at, window, summary FROM runs "
            "WHERE iso_week = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (iso_week,),
        ).fetchone()
        if row is None:
            return None
        run_id, week, status, created_at, window, summary = row
        return {
            "run_id": run_id,
            "iso_week": week,
            "status": status,
            "created_at": created_at,
            "window": json.loads(window),
            "summary": json.loads(summary),
        }

    # Deliveries

    def get_delivery(self, iso_week: str) -> Delivery | None:
        row = self.conn.execute("SELECT data FROM deliveries WHERE iso_week = ?", (iso_week,)).fetchone()
        return Delivery.model_validate_json(row[0]) if row else None

    def save_delivery(self, delivery: Delivery) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO deliveries (iso_week, data) VALUES (?, ?)",
                (delivery.iso_week, delivery.model_dump_json()),
            )
