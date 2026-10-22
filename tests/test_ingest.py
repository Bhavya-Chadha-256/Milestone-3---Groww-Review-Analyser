import hashlib
import logging
import os
import shutil
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from factories import make_review

from pulse.ingest.file_import import FileImportSource
from pulse.ingest.normalize import (
    count_words,
    detect_language,
    make_review_id,
    mark_duplicates,
    mark_exclusions,
    normalize,
    purge_raw,
)
from pulse.models import RawReview
from pulse.privacy.scrub import Scrubber

SINCE = datetime(2026, 7, 26, 18, 30, tzinfo=UTC)
UNTIL = datetime(2026, 10, 18, 18, 30, tzinfo=UTC)
REPO = Path(__file__).resolve().parents[1]
MAPS = REPO / "config" / "mappings"
RAW_FIXTURES = REPO / "tests" / "fixtures" / "raw"
ROW_CONTENT = ("Withdrawal stuck", "Rahul", "Priya", "ABCDE1234F", "Charts are slow", "KYC took")


def fetch_all(  # craftsman-ignore: WARN-PY001, PY003 (regex miscounts the return tuple)
    raw_dir: Path = RAW_FIXTURES,
) -> tuple[list[RawReview], FileImportSource]:
    source = FileImportSource(raw_dir, MAPS)
    return list(source.fetch(SINCE, UNTIL)), source


def by_id(raws: list[RawReview], native_id: str) -> RawReview:
    return next(raw for raw in raws if raw.native_id == native_id)


def test_file_import_handles_messy_csv() -> None:  # ING-1, ING-2, ING-3, ING-4, ING-8
    raws, source = fetch_all()
    messy = [raw for raw in raws if raw.native_id.startswith("r")]
    assert sorted(raw.native_id for raw in messy) == ["r1", "r2", "r3", "r7"]
    assert source.report.skipped == Counter(invalid_rating=1, empty_text=1, out_of_window=1, invalid_date=1)
    assert "\n" in by_id(messy, "r2").text
    assert (by_id(messy, "r2").rating, by_id(messy, "r7").rating) == (4, 3)


def test_empty_optional_fields_become_none() -> None:
    raws, _ = fetch_all()
    quoted = by_id(raws, "r2")
    assert quoted.title is None and quoted.app_version is None


def test_report_counts_rows_read_and_imported() -> None:
    raws, source = fetch_all()
    assert source.report.imported == len(raws) == 10  # 4 messy + 1 titled + 2 utf16 + 3 app store
    assert source.report.rows_read == 14


def test_author_fields_never_reach_review() -> None:  # PII-3
    raws, _ = fetch_all()
    blob = "".join(normalize(raw, Scrubber()).model_dump_json() for raw in raws)
    assert "Rahul Sharma" not in blob and "Priya" not in blob and "img.example" not in blob
    assert "ABCDE1234F" not in blob and "[pan]" in blob


def test_title_is_scrubbed_and_naive_dates_use_mapping_tz() -> None:  # PII-4, ING-9
    raws, _ = fetch_all()
    review = normalize(by_id(raws, "t1"), Scrubber())
    assert review.title == "PAN [pan] issue"
    assert review.posted_at == datetime(2026, 10, 14, 4, 30, tzinfo=UTC)


def test_overlapping_files_import_once(tmp_path: Path) -> None:  # ING-10
    shutil.copy(RAW_FIXTURES / "generic__messy.csv", tmp_path / "generic__a.csv")
    shutil.copy(RAW_FIXTURES / "generic__messy.csv", tmp_path / "generic__b.csv")
    raws, source = fetch_all(tmp_path)
    assert sorted(raw.native_id for raw in raws) == ["r1", "r2", "r3", "r7"]
    assert source.report.duplicates == 4


def test_utf16_export_is_decoded() -> None:  # ING-21
    raws, _ = fetch_all()
    assert sorted(raw.native_id for raw in raws if raw.native_id.startswith("u")) == ["u1", "u2"]


def test_app_store_raw_export_round_trips() -> None:
    raws, _ = fetch_all()
    app_store = [raw for raw in raws if raw.source == "app_store"]
    assert sorted(raw.native_id for raw in app_store) == ["a1", "a2", "a3"]
    assert by_id(app_store, "a1").posted_at == datetime(2026, 10, 12, 8, 0, tzinfo=UTC)


def test_file_without_mapping_is_skipped_with_warning(tmp_path: Path) -> None:  # ING-7
    (tmp_path / "random.csv").write_text("id\n1\n")
    (tmp_path / "nomapping__x.csv").write_text("id\n1\n")
    (tmp_path / ".DS_Store").write_text("")
    shutil.copy(RAW_FIXTURES / "app_store_feed__sample.csv", tmp_path)
    raws, source = fetch_all(tmp_path)
    assert len(raws) == 3
    assert source.report.warnings == ["unmapped_file:nomapping__x.csv", "unmapped_file:random.csv"]


def test_unreadable_or_incomplete_files_are_skipped_with_warning(tmp_path: Path) -> None:
    (tmp_path / "generic__broken.json").write_text('{"not": "a list"}')
    (tmp_path / "generic__columns.csv").write_text("id,text\n1,hello there\n")
    raws, source = fetch_all(tmp_path)
    assert raws == []
    assert source.report.warnings == [
        "unreadable_file:generic__broken.json",
        "missing_columns:generic__columns.csv",
    ]


def test_skip_reasons_never_log_row_content(caplog: pytest.LogCaptureFixture) -> None:  # PII-10
    caplog.set_level(logging.DEBUG)
    _, source = fetch_all()
    logged = caplog.text + repr(source.report)
    assert caplog.text
    for content in ROW_CONTENT:
        assert content not in logged


def test_review_id_is_stable_hash() -> None:
    expected = hashlib.sha256(b"play_store:r1").hexdigest()[:16]
    assert make_review_id("play_store", "r1") == expected
    assert make_review_id("app_store", "r1") != expected


def test_normalize_sets_hash_word_count_and_language() -> None:
    raws, _ = fetch_all()
    review = normalize(by_id(raws, "r3"), Scrubber())
    assert review.text == "my PAN [pan] got rejected" and review.word_count == 5
    assert review.text_hash == hashlib.sha256(b"\nmy PAN [pan] got rejected").hexdigest()[:16]
    assert (review.language, review.review_id) == ("en", make_review_id("play_store", "r3"))
    assert review.excluded_reason is None


@pytest.mark.parametrize(
    "case",
    [
        ("App is great for SIPs", "en"),
        ("पैसे नहीं आए अभी तक", "hi"),
        ("paisa nahi aaya bhai withdrawal kab hoga", "hinglish"),
        ("12345 !!!", "other"),
        ("👍👍", "other"),
        ("பணம் இன்னும் வரவில்லை withdrawal", "other"),  # ING-25
        ("Support said they will fix it but nothing happened", "en"),  # ING-14: no marker words
        ("The app is good but kar is not a word I use", "en"),  # ING-14: one marker stays en
    ],
)
def test_detect_language(case: tuple[str, str]) -> None:  # ING-12, ING-13, ING-14, ING-25
    text, language = case
    assert detect_language(text) == language


def test_count_words_ignores_emoji_and_punctuation() -> None:
    assert count_words("good app 👍 !! [pan] ok") == 4
    assert count_words("   ") == 0


def test_mark_exclusions_flags_short_and_unsupported_language() -> None:  # ING-12, ING-13, ING-24
    reviews = [
        make_review(review_id="eight", word_count=8),
        make_review(review_id="seven", word_count=7),
        make_review(review_id="hindi", language="hi", word_count=12),
        make_review(review_id="mixed", language="hinglish", word_count=9),
    ]
    marked, counts = mark_exclusions(reviews, min_words=8, languages=["en", "hinglish"])
    assert [review.review_id for review in marked] == ["eight", "seven", "hindi", "mixed"]
    assert [review.excluded_reason for review in marked] == [None, "too_short", "unsupported_language", None]
    assert counts == Counter(too_short=1, unsupported_language=1)


def test_mark_exclusions_clears_a_stale_reason() -> None:
    marked, counts = mark_exclusions([make_review(excluded_reason="too_short")], 8, ["en"])
    assert marked[0].excluded_reason is None and counts == Counter()


def test_mark_duplicates_flags_later_copies() -> None:
    early = datetime(2026, 10, 1, tzinfo=UTC)
    reviews = [
        make_review(review_id="later", text="Good app", posted_at=early + timedelta(days=2)),
        make_review(review_id="first", text="good app ", posted_at=early),
        make_review(review_id="other", text="Charts are slow", posted_at=early + timedelta(days=1)),
    ]
    flagged = {review.review_id: review.is_duplicate_text for review in mark_duplicates(reviews)}
    assert flagged == {"later": True, "first": False, "other": False}


def test_purge_raw_deletes_files_older_than_retention(tmp_path: Path) -> None:  # PII-11
    now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    old, fresh = tmp_path / "app_store_feed__old.csv", tmp_path / "generic__fresh.csv"
    old.write_text("x")
    fresh.write_text("x")
    stamp = (now - timedelta(days=15)).timestamp()
    os.utime(old, (stamp, stamp))
    assert purge_raw(tmp_path, 14, now, read_paths=[old, fresh]) == [old]
    assert not old.exists() and fresh.exists()


def test_purge_raw_handles_missing_dir(tmp_path: Path) -> None:
    assert purge_raw(tmp_path / "absent", 14, datetime(2026, 10, 21, tzinfo=UTC), read_paths=[]) == []


def test_newest_file_wins_for_the_same_review(tmp_path: Path) -> None:  # ING-5
    header = "native_id,rating,title,text,posted_at,app_version\n"
    (tmp_path / "play_store_feed__20261001T000000Z.csv").write_text(
        header + "e1,2,,Old text about slow charts on the app,2026-09-30T08:00:00+00:00,\n"
    )
    (tmp_path / "play_store_feed__20261008T000000Z.csv").write_text(
        header + "e1,5,,Edited text now that charts load fast,2026-10-07T08:00:00+00:00,\n"
    )
    raws, source = fetch_all(tmp_path)
    assert [(raw.rating, raw.text) for raw in raws] == [(5, "Edited text now that charts load fast")]
    assert source.report.duplicates == 1 and source.report.imported == 1


def test_read_paths_lists_only_files_that_were_read(tmp_path: Path) -> None:
    shutil.copy(RAW_FIXTURES / "app_store_feed__sample.csv", tmp_path)
    (tmp_path / "random.csv").write_text("id\n1\n")
    (tmp_path / "generic__columns.csv").write_text("id,text\n1,hello there\n")
    _, source = fetch_all(tmp_path)
    assert source.read_paths == [tmp_path / "app_store_feed__sample.csv"]


def test_purge_raw_keeps_files_that_were_not_read(tmp_path: Path) -> None:  # PII-11
    now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    read, unread = tmp_path / "app_store_feed__old.csv", tmp_path / "generic__misnamed.csv"
    stamp = (now - timedelta(days=15)).timestamp()
    for path in (read, unread):
        path.write_text("x")
        os.utime(path, (stamp, stamp))
    assert purge_raw(tmp_path, 14, now, read_paths=[read]) == [read]
    assert unread.exists()
