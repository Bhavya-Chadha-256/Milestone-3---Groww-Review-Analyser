import csv
from datetime import UTC, date, datetime
from pathlib import Path

from pulse.acquire.raw_export import RAW_EXPORT_COLUMNS, raw_status, write_raw_export
from pulse.models import RawReview

REPO_MAPPINGS = Path(__file__).resolve().parents[1] / "config" / "mappings"
NOW = datetime(2026, 10, 21, 5, 30, tzinfo=UTC)


def review(
    native_id: str, posted_at: datetime, text: str = "Withdrawal pending and support slow"
) -> RawReview:
    return RawReview(
        source="app_store",
        native_id=native_id,
        rating=2,
        title="Slow",
        text=text,
        posted_at=posted_at,
        app_version="18.22.0",
    )


def test_write_raw_export_has_only_allowed_columns(tmp_path: Path) -> None:
    path = write_raw_export(
        [review("1", datetime(2026, 10, 14, 17, 0, tzinfo=UTC))], tmp_path / "raw", "app_store_feed", NOW
    )
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert tuple(rows[0].keys()) == RAW_EXPORT_COLUMNS
    assert rows[0]["posted_at"] == "2026-10-14T17:00:00+00:00"


def test_write_raw_export_filename_uses_prefix_and_utc_timestamp(tmp_path: Path) -> None:
    path = write_raw_export([review("1", NOW)], tmp_path / "raw", "app_store_feed", NOW)
    assert path == tmp_path / "raw" / "app_store_feed__20261021T053000Z.csv"


def test_raw_export_round_trips_awkward_text(tmp_path: Path) -> None:
    awkward = 'Line one, with "quotes"\nline two'
    path = write_raw_export([review("1", NOW, text=awkward)], tmp_path, "app_store_feed", NOW)
    with path.open(encoding="utf-8", newline="") as handle:
        assert next(csv.DictReader(handle))["text"] == awkward


def test_raw_status_reports_rows_and_date_range(tmp_path: Path) -> None:
    reviews = [
        review("1", datetime(2026, 10, 10, 15, 0, tzinfo=UTC)),
        review("2", datetime(2026, 10, 14, 17, 0, tzinfo=UTC)),
    ]
    write_raw_export(reviews, tmp_path, "app_store_feed", NOW)
    [status] = raw_status(tmp_path, REPO_MAPPINGS, "Asia/Kolkata")
    assert (status.mapping, status.mapping_found, status.rows, status.problem) == (
        "app_store_feed",
        True,
        2,
        None,
    )
    assert (status.oldest, status.newest) == (date(2026, 10, 10), date(2026, 10, 14))


def test_raw_status_flags_unmapped_files(tmp_path: Path) -> None:  # ING-23
    (tmp_path / "random.csv").write_text("id,text\n1,x\n")
    (tmp_path / "nomapping__x.csv").write_text("id,text\n1,x\n")
    statuses = {status.name: status for status in raw_status(tmp_path, REPO_MAPPINGS, "Asia/Kolkata")}
    assert statuses["random.csv"].mapping is None and statuses["random.csv"].problem
    assert statuses["nomapping__x.csv"].mapping_found is False and statuses["nomapping__x.csv"].problem


def test_raw_status_flags_files_missing_mapped_columns(tmp_path: Path) -> None:
    (tmp_path / "generic__wrong_header.csv").write_text("reviewId,content\n1,x\n")
    [status] = raw_status(tmp_path, REPO_MAPPINGS, "Asia/Kolkata")
    assert status.problem and "id" in status.problem


def test_raw_status_ignores_hidden_files(tmp_path: Path) -> None:
    (tmp_path / ".DS_Store").write_text("")
    assert raw_status(tmp_path, REPO_MAPPINGS, "Asia/Kolkata") == []


def write_generic(directory: Path, name: str, body: str) -> None:
    (directory / name).write_text(body, encoding="utf-8")


def test_raw_status_flags_file_whose_dates_never_parse(tmp_path: Path) -> None:
    write_generic(tmp_path, "generic__excel.csv", "id,rating,title,text,date,version\n1,4,t,ok,45678.5,1.0\n")
    [status] = raw_status(tmp_path, REPO_MAPPINGS, "Asia/Kolkata")
    assert status.problem and "date" in status.problem and status.bad_dates == 1


def test_raw_status_counts_partially_bad_dates(tmp_path: Path) -> None:
    body = "id,rating,title,text,date,version\n1,4,t,ok,2026-10-14 10:00:00,1.0\n2,4,t,ok,14/10/2026,1.0\n"
    write_generic(tmp_path, "generic__mixed.csv", body)
    [status] = raw_status(tmp_path, REPO_MAPPINGS, "Asia/Kolkata")
    assert status.problem is None and status.bad_dates == 1 and status.oldest == date(2026, 10, 14)


def test_raw_status_does_not_require_optional_columns(tmp_path: Path) -> None:
    write_generic(tmp_path, "generic__lean.csv", "id,rating,text,date\n1,4,ok,2026-10-14 10:00:00\n")
    [status] = raw_status(tmp_path, REPO_MAPPINGS, "Asia/Kolkata")
    assert status.problem is None and status.rows == 1


def test_raw_status_flags_invalid_mapping_timezone(tmp_path: Path) -> None:
    mappings = tmp_path / "mappings"
    mappings.mkdir()
    (mappings / "local.yaml").write_text(
        "source: play_store\n"
        "columns: {native_id: id, rating: rating, title: null, text: text,\n"
        "          posted_at: date, app_version: null}\n"
        "date_timezone: IST\n"
    )
    raw = tmp_path / "raw"
    raw.mkdir()
    write_generic(raw, "local__x.csv", "id,rating,text,date\n1,4,ok,2026-10-14\n")
    [status] = raw_status(raw, mappings, "Asia/Kolkata")
    assert status.problem and "invalid" in status.problem


def test_raw_status_flags_json_that_is_not_a_list_of_objects(tmp_path: Path) -> None:
    write_generic(tmp_path, "generic__bad.json", "[1, 2]")
    [status] = raw_status(tmp_path, REPO_MAPPINGS, "Asia/Kolkata")
    assert status.problem and "unreadable" in status.problem
