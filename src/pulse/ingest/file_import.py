"""Read raw export files from data/raw/ through their column mappings (architecture.md §3.1–3.2).

A file named `<mapping>__<anything>.csv|json` is read with config/mappings/<mapping>.yaml.
Only mapped columns are read, so author and device columns never leave the file.
Logs and the ImportReport carry counts and reasons only (PII-10).
"""

import csv
import logging
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pulse.ingest.base import ImportReport
from pulse.ingest.mapping import NULLABLE_FIELDS, Mapping, load_mapping, mapping_name_for, read_rows
from pulse.models import RawReview

logger = logging.getLogger(__name__)

EXPORT_SUFFIXES = (".csv", ".json")
RATING_RE = re.compile(r"\s*([1-5])(?:\.0+)?\s*(?:stars?)?\s*", re.IGNORECASE)


class SkipRow(Exception):
    """A row that can't become a RawReview; `reason` is a counted skip reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class FileImportSource:
    name = "file"

    def __init__(self, raw_dir: Path, mappings_dir: Path) -> None:
        self.raw_dir = Path(raw_dir)
        self.mappings_dir = Path(mappings_dir)
        self.report = ImportReport()
        self.read_paths: list[Path] = []

    def fetch(self, since: datetime, until: datetime) -> Iterator[RawReview]:
        """Yield each in-window review once per (source, native_id); fills a fresh `self.report`.

        Files are read in name order and a later file's copy replaces an earlier one, so the newest
        `pulse fetch` export (`<prefix>__<UTC timestamp>`) wins and edits reach the store (ING-5, ING-10).
        """
        self.report = ImportReport()
        self.read_paths = []
        latest: dict[tuple[str, str], RawReview] = {}
        for path in self._export_files():
            for raw in self._read_file(path, since, until):
                key = (raw.source, raw.native_id)
                self.report.duplicates += key in latest
                latest[key] = raw
        self.report.imported = len(latest)
        yield from latest.values()

    def _export_files(self) -> list[Path]:
        if not self.raw_dir.is_dir():
            return []
        return [
            path
            for path in sorted(self.raw_dir.iterdir())
            if path.is_file() and not path.name.startswith(".")
        ]

    def _read_file(self, path: Path, since: datetime, until: datetime) -> Iterator[RawReview]:
        mapping = self._mapping_for(path)
        if mapping is None:
            return
        try:
            rows = read_rows(path)
        except (UnicodeError, ValueError, csv.Error):
            self._warn("unreadable_file", path)
            return
        if rows and any(column not in rows[0] for column in mapping.required_columns()):
            self._warn("missing_columns", path)
            return
        self.read_paths.append(path)
        before = self.report.skipped.total()
        for row in rows:
            self.report.rows_read += 1
            try:
                yield _to_raw(_mapped_fields(row, mapping), mapping, since, until)
            except SkipRow as skip:
                self.report.skipped[skip.reason] += 1
        logger.info(
            "read %s: %d rows, %d skipped", path.name, len(rows), self.report.skipped.total() - before
        )

    def _mapping_for(self, path: Path) -> Mapping | None:
        name = mapping_name_for(path)
        if name is None or path.suffix.lower() not in EXPORT_SUFFIXES:
            self._warn("unmapped_file", path)
            return None
        try:
            return load_mapping(self.mappings_dir, name)
        except (FileNotFoundError, ValidationError):
            self._warn("unmapped_file", path)
            return None

    def _warn(self, reason: str, path: Path) -> None:
        self.report.warnings.append(f"{reason}:{path.name}")
        logger.warning("skipped file %s: %s", path.name, reason)


def _mapped_fields(row: dict[str, Any], mapping: Mapping) -> dict[str, Any]:
    """Only the mapped columns, keyed by our field names; blank nullable values become None."""
    fields: dict[str, Any] = {}
    for name, column in mapping.columns.items():
        value = row.get(column) if column else None
        if isinstance(value, str) and not value.strip() and name in NULLABLE_FIELDS:
            value = None
        fields[name] = value
    return fields


def _to_raw(fields: dict[str, Any], mapping: Mapping, since: datetime, until: datetime) -> RawReview:
    rating = _parse_rating(fields["rating"])
    if rating is None:
        raise SkipRow("invalid_rating")
    text = fields["text"]
    if not isinstance(text, str) or not text.strip():
        raise SkipRow("empty_text")
    posted_at = mapping.parse_timestamp(fields["posted_at"]) if fields["posted_at"] is not None else None
    if posted_at is None:
        raise SkipRow("invalid_date")
    if not since <= posted_at < until:
        raise SkipRow("out_of_window")
    try:
        return RawReview(
            source=mapping.source,
            native_id=str(fields["native_id"] or "").strip(),
            rating=rating,
            title=_optional_text(fields["title"]),
            text=text,
            posted_at=posted_at,
            app_version=_optional_text(fields["app_version"]),
        )
    except ValidationError as error:
        raise SkipRow("missing_id") from error


def _parse_rating(value: Any) -> int | None:
    """1–5 from an int, "4", "4.0" or "4 stars"; None for anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value) if value == int(value) and 1 <= value <= 5 else None
    match = RATING_RE.fullmatch(value) if isinstance(value, str) else None
    return int(match.group(1)) if match else None


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)
