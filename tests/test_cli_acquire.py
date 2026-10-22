from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from pulse import cli
from pulse.acquire.app_store_feed import FetchReport
from pulse.acquire.raw_export import RAW_EXPORT_COLUMNS
from pulse.config import Settings
from pulse.ingest.mapping import load_mapping
from pulse.models import RawReview

SECRET_TEXT = "Withdrawal pending for three days"


class FakeFeedClient:
    def __init__(self, reviews: list[RawReview]) -> None:
        self.reviews = reviews
        self.since: datetime | None = None

    def fetch(self, since: datetime) -> tuple[list[RawReview], FetchReport]:
        self.since = since
        report = FetchReport(
            pages_requested=3, pages_with_entries=2, pages_empty=1, reviews=len(self.reviews)
        )
        report.warnings.append("app_store_feed_empty_pages:1")
        return self.reviews, report


def sample_reviews() -> list[RawReview]:
    return [
        RawReview(
            source="app_store",
            native_id=str(index),
            rating=2,
            title="Slow",
            text=SECRET_TEXT,
            posted_at=datetime(2026, 10, 10 + index, 9, 0, tzinfo=UTC),
            app_version="18.22.0",
        )
        for index in range(2)
    ]


def write_config(settings: Settings, path: Path) -> Path:
    path.write_text(yaml.safe_dump(settings.model_dump(mode="json")), encoding="utf-8")
    return path


@pytest.fixture
def app_settings(tmp_settings: Settings) -> Settings:
    product = tmp_settings.product.model_copy(update={"app_store_id": "1404871703"})
    sources = tmp_settings.sources.model_copy(
        update={"app_store": tmp_settings.sources.app_store.model_copy(update={"enabled": True})}
    )
    return tmp_settings.model_copy(update={"product": product, "sources": sources})


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeFeedClient:
    client = FakeFeedClient(sample_reviews())
    monkeypatch.setattr(cli, "make_feed_client", lambda settings: client)
    monkeypatch.setattr(cli, "now_utc", lambda: datetime(2026, 10, 21, 5, 30, tzinfo=UTC))
    return client


def run_cli(config: Path, *args: str) -> object:
    return CliRunner().invoke(cli.app, ["--config", str(config), *args])


def test_fetch_writes_raw_export(app_settings: Settings, fake_client: FakeFeedClient, tmp_path: Path) -> None:
    result = run_cli(write_config(app_settings, tmp_path / "pulse.yaml"), "fetch", "app-store")
    assert result.exit_code == 0, result.output
    files = sorted(Path(app_settings.paths.raw).iterdir())
    assert [file.name for file in files] == ["app_store_feed__20261021T053000Z.csv"]
    assert "2 reviews" in result.output and "app_store_feed_empty_pages:1" in result.output


def test_fetch_refuses_placeholder_app_id(
    app_settings: Settings, fake_client: FakeFeedClient, tmp_path: Path
) -> None:  # ING-22
    product = app_settings.product.model_copy(update={"app_store_id": "<GROWW_IOS_APP_ID>"})
    placeholder = app_settings.model_copy(update={"product": product})
    result = run_cli(write_config(placeholder, tmp_path / "pulse.yaml"), "fetch", "app-store")
    assert result.exit_code == 2
    assert not Path(app_settings.paths.raw).exists() or not any(Path(app_settings.paths.raw).iterdir())


def test_fetch_refuses_disabled_source(
    app_settings: Settings, fake_client: FakeFeedClient, tmp_path: Path
) -> None:
    sources = app_settings.sources.model_copy(
        update={"app_store": app_settings.sources.app_store.model_copy(update={"enabled": False})}
    )
    disabled = app_settings.model_copy(update={"sources": sources})
    assert run_cli(write_config(disabled, tmp_path / "pulse.yaml"), "fetch", "app-store").exit_code == 2


def test_fetch_summary_never_prints_review_text(
    app_settings: Settings, fake_client: FakeFeedClient, tmp_path: Path
) -> None:
    result = run_cli(write_config(app_settings, tmp_path / "pulse.yaml"), "fetch", "app-store")
    assert SECRET_TEXT not in result.output


def test_fetch_uses_ist_today(  # craftsman-ignore: WARN-PY001 (pytest fixtures)
    app_settings: Settings, fake_client: FakeFeedClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:  # OPS-8
    monkeypatch.setattr(cli, "now_utc", lambda: datetime(2026, 10, 18, 19, 30, tzinfo=UTC))
    run_cli(write_config(app_settings, tmp_path / "pulse.yaml"), "fetch", "app-store")
    assert fake_client.since == datetime(2026, 7, 26, 18, 30, tzinfo=UTC)


def test_fetch_with_no_reviews_writes_nothing(
    app_settings: Settings, fake_client: FakeFeedClient, tmp_path: Path
) -> None:
    fake_client.reviews = []
    result = run_cli(write_config(app_settings, tmp_path / "pulse.yaml"), "fetch", "app-store")
    assert result.exit_code == 1 and "no reviews" in result.output.lower()
    assert not Path(app_settings.paths.raw).exists() or not any(Path(app_settings.paths.raw).iterdir())


def test_raw_status_lists_files_and_flags_problems(
    app_settings: Settings, fake_client: FakeFeedClient, tmp_path: Path
) -> None:
    config = write_config(app_settings, tmp_path / "pulse.yaml")
    run_cli(config, "fetch", "app-store")
    ok = run_cli(config, "raw", "status")
    assert ok.exit_code == 0 and "app_store_feed__20261021T053000Z.csv" in ok.output and "rows=2" in ok.output
    (Path(app_settings.paths.raw) / "random.csv").write_text("id\n1\n")
    bad = run_cli(config, "raw", "status")
    assert bad.exit_code == 1 and "random.csv" in bad.output


@pytest.fixture
def play_settings(tmp_settings: Settings) -> Settings:
    sources = tmp_settings.sources.model_copy(
        update={"play_store": tmp_settings.sources.play_store.model_copy(update={"enabled": True})}
    )
    return tmp_settings.model_copy(update={"sources": sources})


@pytest.fixture
def fake_play(monkeypatch: pytest.MonkeyPatch) -> FakeFeedClient:
    client = FakeFeedClient(sample_reviews())
    monkeypatch.setattr(cli, "make_play_client", lambda settings: client)
    monkeypatch.setattr(cli, "now_utc", lambda: datetime(2026, 10, 21, 5, 30, tzinfo=UTC))
    return client


def test_fetch_play_store_writes_raw_export(
    play_settings: Settings, fake_play: FakeFeedClient, tmp_path: Path
) -> None:
    result = run_cli(write_config(play_settings, tmp_path / "pulse.yaml"), "fetch", "play-store")
    assert result.exit_code == 0, result.output
    files = sorted(Path(play_settings.paths.raw).iterdir())
    assert [file.name for file in files] == ["play_store_feed__20261021T053000Z.csv"]
    assert "2 reviews" in result.output and SECRET_TEXT not in result.output
    assert fake_play.since == datetime(2026, 7, 26, 18, 30, tzinfo=UTC)


def test_fetch_play_store_refuses_when_disabled(
    play_settings: Settings, fake_play: FakeFeedClient, tmp_path: Path
) -> None:
    sources = play_settings.sources.model_copy(
        update={"play_store": play_settings.sources.play_store.model_copy(update={"enabled": False})}
    )
    disabled = play_settings.model_copy(update={"sources": sources})
    assert run_cli(write_config(disabled, tmp_path / "pulse.yaml"), "fetch", "play-store").exit_code == 2


def test_fetch_play_store_with_no_reviews_writes_nothing(
    play_settings: Settings, fake_play: FakeFeedClient, tmp_path: Path
) -> None:
    fake_play.reviews = []
    result = run_cli(write_config(play_settings, tmp_path / "pulse.yaml"), "fetch", "play-store")
    assert result.exit_code == 1 and "no reviews" in result.output.lower()
    assert not Path(play_settings.paths.raw).exists() or not any(Path(play_settings.paths.raw).iterdir())


def test_play_store_feed_mapping_reads_play_exports(play_settings: Settings) -> None:
    mapping = load_mapping(Path(play_settings.paths.mappings), "play_store_feed")
    assert mapping.source == "play_store" and list(mapping.columns.values()) == list(RAW_EXPORT_COLUMNS)
