"""Column mappings for raw export files, plus the shared file reader and timestamp parser.

A file in data/raw/ named `<mapping>__<anything>.csv|json` is read with config/mappings/<mapping>.yaml,
which maps our logical fields to the export's column names (architecture.md §3.1).
"""

import codecs
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from pulse.models import Source

MAPPED_FIELDS = ("native_id", "rating", "title", "text", "posted_at", "app_version")
NULLABLE_FIELDS = frozenset({"title", "app_version"})


class Mapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Source
    columns: dict[str, str | None]
    date_format: str | None = None  # None = ISO 8601
    date_timezone: str = "UTC"  # applied to timestamps without an offset

    @field_validator("date_timezone")
    @classmethod
    def timezone_exists(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(
                f"unknown date_timezone {value!r}; use an IANA name such as Asia/Kolkata"
            ) from error
        return value

    @model_validator(mode="after")
    def columns_cover_every_field(self) -> Self:
        missing = [name for name in MAPPED_FIELDS if name not in self.columns]
        unknown = sorted(set(self.columns) - set(MAPPED_FIELDS))
        unmapped = [
            name for name in MAPPED_FIELDS if name not in NULLABLE_FIELDS and not self.columns.get(name)
        ]
        if missing or unknown or unmapped:
            raise ValueError(
                f"mapping columns must cover {MAPPED_FIELDS}; missing={missing}, unknown={unknown}, "
                f"null but required={unmapped}"
            )
        return self

    def required_columns(self) -> list[str]:
        """Export columns that must be present: the mapped non-nullable fields."""
        return [column for name, column in self.columns.items() if column and name not in NULLABLE_FIELDS]

    def parse_timestamp(self, value: str) -> datetime | None:
        text = str(value).strip()
        try:
            parsed = (
                datetime.strptime(text, self.date_format)
                if self.date_format
                else datetime.fromisoformat(text)
            )
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(self.date_timezone))
        return parsed.astimezone(UTC)


def load_mapping(mappings_dir: Path, name: str) -> Mapping:
    path = Path(mappings_dir) / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"mapping '{name}' not found: {path}")
    return Mapping.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def mapping_name_for(path: Path) -> str | None:
    if "__" not in path.name:
        return None
    return path.name.split("__", 1)[0] or None


def detect_encoding(path: Path) -> str:
    """UTF-16 when the file starts with a UTF-16 BOM (console-style exports), else UTF-8 (BOM optional)."""
    with path.open("rb") as handle:
        head = handle.read(2)
    if head in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        return "utf-16"
    return "utf-8-sig"


def read_rows(path: Path) -> list[dict[str, Any]]:
    """All rows of a CSV or JSON (list of objects) export, as dicts keyed by the export's own column names."""
    encoding = detect_encoding(path)
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding=encoding))
        if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
            raise ValueError("JSON export must be a list of review objects")
        return [dict(row) for row in data]
    with path.open(encoding=encoding, newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]
