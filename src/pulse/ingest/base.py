"""The ingest adapter contract (architecture.md §3.1)."""

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from pulse.models import RawReview


@dataclass
class ImportReport:
    """Counts and reasons only, never row content (PII-10)."""

    rows_read: int = 0
    imported: int = 0
    duplicates: int = 0  # (source, native_id) already seen in this import (ING-10)
    skipped: Counter[str] = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)


class ReviewSource(Protocol):
    name: str
    report: ImportReport

    def fetch(self, since: datetime, until: datetime) -> Iterator[RawReview]: ...
