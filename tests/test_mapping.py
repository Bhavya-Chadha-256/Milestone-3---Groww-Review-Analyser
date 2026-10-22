import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from pulse.ingest.mapping import load_mapping, mapping_name_for, read_rows

REPO_MAPPINGS = Path(__file__).resolve().parents[1] / "config" / "mappings"


def write_mapping(
    directory: Path, name: str, columns: dict[str, str | None]
) -> None:  # craftsman-ignore: WARN-PY001
    body = {"source": "play_store", "columns": columns, "date_timezone": "Asia/Kolkata"}
    (directory / f"{name}.yaml").write_text(yaml.safe_dump(body))


GOOD_COLUMNS: dict[str, str | None] = {
    "native_id": "id",
    "rating": "rating",
    "title": None,
    "text": "text",
    "posted_at": "date",
    "app_version": None,
}


def test_repo_mappings_load() -> None:
    generic = load_mapping(REPO_MAPPINGS, "generic")
    assert (generic.source, generic.columns["text"], generic.date_timezone) == (
        "play_store",
        "text",
        "Asia/Kolkata",
    )
    feed = load_mapping(REPO_MAPPINGS, "app_store_feed")
    assert feed.source == "app_store" and feed.columns["posted_at"] == "posted_at"


def test_load_mapping_rejects_missing_column_key(tmp_path: Path) -> None:
    write_mapping(
        tmp_path, "broken", {key: value for key, value in GOOD_COLUMNS.items() if key != "posted_at"}
    )
    with pytest.raises(ValidationError, match="posted_at"):
        load_mapping(tmp_path, "broken")


def test_load_mapping_rejects_null_required_column(tmp_path: Path) -> None:
    write_mapping(tmp_path, "broken", {**GOOD_COLUMNS, "text": None})
    with pytest.raises(ValidationError, match="text"):
        load_mapping(tmp_path, "broken")


def test_load_mapping_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_mapping(tmp_path, "nope")


@pytest.mark.parametrize(
    "case",
    [
        ("generic__appbot.csv", "generic"),
        ("app_store_feed__20261021T053000Z.csv", "app_store_feed"),
        ("random.csv", None),
        ("__x.csv", None),
    ],
)
def test_mapping_name_for(case: tuple[str, str | None]) -> None:
    filename, expected = case
    assert mapping_name_for(Path(filename)) == expected


def test_parse_timestamp_uses_mapping_timezone_for_naive_values(tmp_path: Path) -> None:
    write_mapping(tmp_path, "local", GOOD_COLUMNS)
    mapping = load_mapping(tmp_path, "local")
    assert mapping.parse_timestamp("2026-10-14 10:00:00") == datetime(2026, 10, 14, 4, 30, tzinfo=UTC)
    assert mapping.parse_timestamp("2026-10-14T10:00:00-07:00") == datetime(2026, 10, 14, 17, 0, tzinfo=UTC)
    assert mapping.parse_timestamp("not-a-date") is None


def test_read_rows_handles_csv_bom_utf16_and_json(tmp_path: Path) -> None:  # ING-21
    (tmp_path / "a.csv").write_bytes("﻿id,text\r\n1,hello\r\n".encode())
    (tmp_path / "b.csv").write_text("id,text\n2,नमस्ते\n", encoding="utf-16")
    (tmp_path / "c.json").write_text(json.dumps([{"id": "3", "text": "hi"}]))
    assert read_rows(tmp_path / "a.csv") == [{"id": "1", "text": "hello"}]
    assert read_rows(tmp_path / "b.csv") == [{"id": "2", "text": "नमस्ते"}]
    assert read_rows(tmp_path / "c.json") == [{"id": "3", "text": "hi"}]


def test_load_mapping_rejects_unknown_timezone(tmp_path: Path) -> None:
    body = {"source": "play_store", "columns": GOOD_COLUMNS, "date_timezone": "IST"}
    (tmp_path / "local.yaml").write_text(yaml.safe_dump(body))
    with pytest.raises(ValidationError, match="IST"):
        load_mapping(tmp_path, "local")


def test_read_rows_rejects_json_non_objects(tmp_path: Path) -> None:
    (tmp_path / "x.json").write_text("[1, 2]")
    with pytest.raises(ValueError):
        read_rows(tmp_path / "x.json")
