"""The hosted, read-only dashboard on Render (Task 7.5): public hosts, snapshot export and import."""

import gzip
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from conftest import dashboard_client
from pydantic import ValidationError
from test_cli import invoke
from test_pipeline_deliver import deliver, pulse_llm
from test_pipeline_pulse import seed
from web_data import write

from pulse.config import DashboardSettings, Settings
from pulse.models import RunStatus
from pulse.store import Store
from pulse.timeutil import window_for_week
from pulse.web.pulse_view import pulse_view
from pulse.web.snapshot import export_snapshot, unpack_snapshot

RENDER_HOST = "groww-review-pulse.onrender.com"


def hosted(settings: Settings) -> Settings:
    dashboard = DashboardSettings(host="0.0.0.0", read_only=True, public_hosts=[RENDER_HOST])
    return settings.model_copy(update={"dashboard": dashboard})


def test_public_host_requires_read_only_and_named_hosts() -> None:
    with pytest.raises(ValidationError, match="read-only"):
        DashboardSettings(host="0.0.0.0", public_hosts=[RENDER_HOST])
    with pytest.raises(ValidationError, match="public_hosts"):
        DashboardSettings(host="0.0.0.0", read_only=True)
    assert DashboardSettings(host="0.0.0.0", read_only=True, public_hosts=[RENDER_HOST]).read_only


def test_public_host_header_allowed_and_others_refused(tmp_settings: Settings) -> None:
    write(Path(tmp_settings.paths.db))
    client = dashboard_client(hosted(tmp_settings))
    assert client.get("/api/meta", headers={"host": RENDER_HOST}).status_code == 200
    assert client.get("/api/meta", headers={"host": "evil.example"}).status_code == 400


def test_read_only_refuses_jobs_and_never_checks_delivery(tmp_settings: Settings) -> None:
    write(Path(tmp_settings.paths.db))

    def no_check() -> None:
        raise AssertionError("a read-only dashboard must not contact the MCP server")

    client = dashboard_client(hosted(tmp_settings), readiness=no_check)
    meta = client.get("/api/meta").json()
    assert meta["read_only"] is True and meta["classified"] == 6 and meta["data_updated_at"]
    job = client.post(
        "/api/jobs", json={"action": "generate", "week": "2026-W42"}, headers={"X-Pulse-Client": "dashboard"}
    )
    assert job.status_code == 403 and "read-only" in job.json()["error"]
    ready = client.get("/api/delivery-ready").json()
    assert ready["ready"] is False and "read-only" in ready["problems"][0]["detail"]


def test_default_week_follows_the_latest_data(tmp_settings: Settings) -> None:
    write(Path(tmp_settings.paths.db))  # the newest sample review is Thu 15 Oct, so W42 isn't complete
    later = datetime(2026, 11, 10, 6, tzinfo=UTC)
    client = dashboard_client(tmp_settings, now=lambda: later)
    client.app.state.dashboard.today = None  # production: no pinned date
    assert client.get("/api/meta").json()["week"] == "2026-W41"


def test_pulse_view_prefers_the_latest_run_with_a_pulse(tmp_settings: Settings, tmp_store: Store) -> None:
    seed(tmp_store)
    deliver(tmp_settings, tmp_store, pulse_llm(), None, until_stage="validate")
    later = window_for_week("2026-W42", tmp_settings.window.weeks, tmp_settings.window.timezone)
    tmp_store.create_run("2026-W42-20261022T090923", later)  # a backfill run that stopped early
    tmp_store.set_run_status("2026-W42-20261022T090923", RunStatus.FAILED)
    view = pulse_view(Store.open_readonly(Path(tmp_settings.paths.db)), tmp_settings, "2026-W42")
    assert view is not None and view["status"] == "VALIDATED" and view["has_pulse"]
    assert view["latest_status"] == "FAILED"


def test_snapshot_round_trip(tmp_settings: Settings, tmp_store: Store, tmp_path: Path) -> None:
    seed(tmp_store)
    out = deliver(tmp_settings, tmp_store, pulse_llm(), None, until_stage="validate")
    snapshot = tmp_path / "snapshot"
    report = export_snapshot(tmp_settings, snapshot)
    assert (snapshot / "pulse.db.gz").is_file() and not (snapshot / "pulse.db").exists()
    copied = snapshot / "runs" / out.run_id
    assert sorted(item.name for item in copied.iterdir()) == sorted(
        [
            "aggregates.json",
            "taxonomy.json",
            "quotes.json",
            "pulse_draft.json",
            "validation.json",
            "pulse.md",
            "report.json",
        ]
    )
    assert (
        report.runs == [out.run_id]
        and report.reviews == tmp_store.conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    )
    database = unpack_snapshot(snapshot, tmp_path / "work")
    assert (
        Store.open_readonly(database).conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
        == report.reviews
    )
    assert gzip.decompress((snapshot / "pulse.db.gz").read_bytes())[:15] == b"SQLite format 3"


def test_serve_public_binds_all_interfaces_read_only(  # craftsman-ignore: PY003 (hint on closing line)
    tmp_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(Path(tmp_settings.paths.db))
    export_snapshot(tmp_settings, tmp_path / "snapshot")
    seen: dict[str, Any] = {}

    def fake_run(app: Any, **options: Any) -> None:
        seen.update(options, settings=app.state.dashboard.settings)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", RENDER_HOST)
    monkeypatch.setenv("PORT", "10000")
    result = invoke(tmp_settings, tmp_path, "serve", "--public", "--snapshot", str(tmp_path / "snapshot"))
    assert result.exit_code == 0, result.output
    dashboard = seen["settings"].dashboard
    assert seen["host"] == "0.0.0.0" and seen["port"] == 10000 and seen["access_log"] is False
    assert dashboard.read_only and dashboard.public_hosts == [RENDER_HOST]
    assert Path(seen["settings"].paths.db).is_file()
