"""Write raw export files into data/raw/ and report what is there before normalising.

Raw exports written by `pulse fetch` contain only RAW_EXPORT_COLUMNS: never author/user/device fields.
"""

import csv
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from pulse.ingest.mapping import Mapping, load_mapping, mapping_name_for, read_rows
from pulse.models import RawReview

RAW_EXPORT_COLUMNS = ("native_id", "rating", "title", "text", "posted_at", "app_version")
EXPORT_SUFFIXES = (".csv", ".json")


def write_raw_export(reviews: list[RawReview], raw_dir: Path, prefix: str, now: datetime) -> Path:
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{prefix}__{now.astimezone(UTC):%Y%m%dT%H%M%SZ}.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RAW_EXPORT_COLUMNS)
        writer.writeheader()
        for review in reviews:
            writer.writerow(
                {
                    "native_id": review.native_id,
                    "rating": review.rating,
                    "title": review.title or "",
                    "text": review.text,
                    "posted_at": review.posted_at.isoformat(),
                    "app_version": review.app_version or "",
                }
            )
    return path


@dataclass(frozen=True)
class RawFileStatus:
    name: str
    mapping: str | None
    mapping_found: bool
    rows: int | None
    oldest: date | None
    newest: date | None
    problem: str | None
    bad_dates: int = 0


def raw_status(raw_dir: Path, mappings_dir: Path, timezone: str) -> list[RawFileStatus]:
    raw_dir = Path(raw_dir)
    if not raw_dir.is_dir():
        return []
    files = [path for path in sorted(raw_dir.iterdir()) if path.is_file() and not path.name.startswith(".")]
    return [_file_status(path, Path(mappings_dir), timezone) for path in files]


def _file_status(path: Path, mappings_dir: Path, timezone: str) -> RawFileStatus:
    name = mapping_name_for(path)
    if name is None or path.suffix.lower() not in EXPORT_SUFFIXES:
        return RawFileStatus(path.name, name, False, None, None, None, _NAMING_HINT)
    try:
        mapping = load_mapping(mappings_dir, name)
    except FileNotFoundError:
        return RawFileStatus(
            path.name, name, False, None, None, None, f"mapping '{name}' not found in {mappings_dir}"
        )
    except ValidationError as error:
        return RawFileStatus(
            path.name, name, False, None, None, None, f"mapping '{name}' is invalid: {error}"
        )
    try:
        rows = read_rows(path)
    except (UnicodeError, ValueError, csv.Error) as error:
        return RawFileStatus(path.name, name, True, None, None, None, f"unreadable: {type(error).__name__}")
    return _rows_status(path.name, name, mapping, rows, timezone)


def _rows_status(  # craftsman-ignore: WARN-PY001 (private helper; all inputs are distinct facts)
    filename: str, name: str, mapping: Mapping, rows: list[dict], timezone: str
) -> RawFileStatus:
    header = set().union(*rows) if rows else set()
    missing = [column for column in mapping.required_columns() if column not in header]
    if rows and missing:
        return RawFileStatus(
            filename, name, True, len(rows), None, None, f"missing mapped columns: {missing}"
        )
    zone = ZoneInfo(timezone)
    posted_column = mapping.columns["posted_at"]
    stamps = [mapping.parse_timestamp(row.get(posted_column) or "") for row in rows]
    days = sorted(stamp.astimezone(zone).date() for stamp in stamps if stamp is not None)
    bad_dates = len(stamps) - len(days)
    problem = None
    if not rows:
        problem = "no rows"
    elif not days:
        problem = f"no parseable dates in column '{posted_column}'; check date_format/date_timezone"
    return RawFileStatus(
        filename,
        name,
        True,
        len(rows),
        days[0] if days else None,
        days[-1] if days else None,
        problem,
        bad_dates,
    )


_NAMING_HINT = "no mapping prefix: name the file <mapping>__<name>.csv|json (e.g. generic__play_export.csv)"
