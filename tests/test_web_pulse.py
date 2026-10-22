import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import DASHBOARD_URL, dashboard_client
from test_pipeline_deliver import deliver, pulse_llm
from test_pipeline_pulse import seed
from web_data import write

from pulse.config import Settings
from pulse.report.words import count_words
from pulse.store import Store
from pulse.web.jobs import ACTIONS, JobRunner
from pulse.web.pulse_view import Readiness, pulse_view

WEEK = "2026-W42"
CLIENT = {"X-Pulse-Client": "dashboard"}


@dataclass
class FakeProcess:
    exit_code: int | None = 0  # None: still running
    stdout: str = "SECRET REVIEW TEXT from the pipeline"

    def poll(self) -> int | None:
        return self.exit_code


@dataclass
class FakeLauncher:
    process: FakeProcess = field(default_factory=FakeProcess)
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, args: list[str]) -> FakeProcess:
        self.calls.append(args)
        return self.process


def post_job(
    client: Any, action: str, week: str = WEEK, **headers: str
) -> Any:  # craftsman-ignore: WARN-PY001 (test helper)
    return client.post("/api/jobs", json={"action": action, "week": week}, headers=CLIENT | headers)


def jobs_client(tmp_settings: Settings, launcher: FakeLauncher, **hooks: Any) -> Any:
    write(Path(tmp_settings.paths.db))
    return dashboard_client(tmp_settings, launch=launcher, **hooks)


def test_pulse_view_matches_rendered_markdown(tmp_settings: Settings, tmp_store: Store) -> None:
    seed(tmp_store)
    out = deliver(tmp_settings, tmp_store, pulse_llm(), None, until_stage="validate")
    view = pulse_view(Store.open_readonly(Path(tmp_settings.paths.db)), tmp_settings, WEEK)
    markdown = (out.run_dir / "pulse.md").read_text()
    assert view is not None and view["status"] == "VALIDATED" and view["markdown"] == markdown
    assert view["title"] in markdown and view["meta_line"] in markdown and len(view["themes"]) == 3
    for theme in view["themes"]:
        assert (
            theme["name"] in markdown
            and f"{theme['share_pct']}%" in markdown
            and theme["summary"] in markdown
        )
    assert all(quote["text"] in markdown and quote["theme_id"] for quote in view["quotes"])
    assert view["max_words"] == tmp_settings.pulse.max_words
    assert all(action["text"] in markdown for action in view["actions"])
    assert view["word_count"] == count_words(markdown)
    assert view["checks"] == {f"V{number}": True for number in range(1, 8)}


def test_pulse_view_without_run_is_empty(web_client: Any) -> None:
    response = web_client.get("/api/pulse", params={"week": WEEK})
    assert response.status_code == 404 and response.json() == {"empty": True}


def test_jobs_use_fixed_argument_table() -> None:
    launcher = FakeLauncher()
    runner = JobRunner(launcher, status_of=lambda week: None)
    for action in ("generate", "publish", "draft"):
        runner.start(action, WEEK)
    prefix = [sys.executable, "-m", "pulse.cli"]
    assert launcher.calls == [
        [*prefix, "run", "--week", WEEK, "--dry-run"],
        [*prefix, "run", "--week", WEEK, "--from", "publish", "--until", "publish"],
        [*prefix, "run", "--week", WEEK, "--from", "draft"],
    ]
    assert set(ACTIONS) == {"generate", "publish", "draft"}


def test_invalid_week_rejected_before_launch(tmp_settings: Settings) -> None:
    launcher = FakeLauncher()
    client = jobs_client(tmp_settings, launcher)
    for week in ("2026-W99", "<script>", "2026-42"):
        assert post_job(client, "generate", week).status_code == 422
    assert post_job(client, "send_email").status_code == 422
    assert launcher.calls == []


def test_second_job_while_running_returns_409(tmp_settings: Settings) -> None:
    client = jobs_client(tmp_settings, FakeLauncher(FakeProcess(exit_code=None)))
    first = post_job(client, "generate")
    assert first.status_code == 202 and first.json()["state"] == "running"
    assert post_job(client, "generate").status_code == 409


def test_locked_pipeline_reports_locked() -> None:  # DEL-11
    runner = JobRunner(FakeLauncher(FakeProcess(exit_code=3)), status_of=lambda week: None)
    job = runner.start("generate", WEEK)
    assert runner.get(job.id).state == "locked"


def test_post_requires_client_header_and_same_origin(tmp_settings: Settings) -> None:  # CSRF
    launcher = FakeLauncher()
    client = jobs_client(tmp_settings, launcher)
    assert client.post("/api/jobs", json={"action": "generate", "week": WEEK}).status_code == 403
    assert post_job(client, "generate", Origin="http://evil.example").status_code == 403
    assert post_job(client, "generate", Origin=DASHBOARD_URL).status_code == 202
    assert len(launcher.calls) == 1


def test_delivery_actions_blocked_until_doctor_passes(tmp_settings: Settings) -> None:
    blocked = Readiness(problems=[("mcp_tools", "MCP_AUTH_TOKEN is not set")], draft_supported=True)
    launcher = FakeLauncher()
    client = jobs_client(tmp_settings, launcher, readiness=lambda: blocked)
    ready = client.get("/api/delivery-ready").json()
    assert ready == {
        "ready": False,
        "problems": [{"name": "mcp_tools", "detail": "MCP_AUTH_TOKEN is not set"}],
        "draft_supported": True,
    }
    assert post_job(client, "publish").status_code == 409 and post_job(client, "draft").status_code == 409
    assert post_job(client, "generate").status_code == 202 and len(launcher.calls) == 1


def test_draft_blocked_when_server_has_no_draft_tool(tmp_settings: Settings) -> None:  # DEL-15, WEB-15
    append_only = Readiness(problems=[], draft_supported=False)
    client = jobs_client(tmp_settings, FakeLauncher(), readiness=lambda: append_only)
    response = post_job(client, "draft")
    assert response.status_code == 409 and "no Gmail draft tool" in response.json()["error"]
    assert post_job(client, "publish").status_code == 202


def test_job_status_never_includes_process_output(tmp_settings: Settings) -> None:
    client = jobs_client(tmp_settings, FakeLauncher())
    job = post_job(client, "generate").json()
    status = client.get(f"/api/jobs/{job['id']}")
    assert status.status_code == 200 and "SECRET" not in status.text
    assert set(status.json()) == {"id", "action", "iso_week", "state", "exit_code", "run_status", "warnings"}
    assert client.get("/api/jobs/unknown").status_code == 404


def test_budget_reports_next_reset_in_ist(tmp_settings: Settings) -> None:
    write(Path(tmp_settings.paths.db))
    store = Store(Path(tmp_settings.paths.db))
    store.add_llm_usage("2026-10-22", tmp_settings.llm.model, 150_000)
    store.conn.close()
    noon = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    budget = dashboard_client(tmp_settings, now=lambda: noon).get("/api/llm-budget").json()
    assert budget["models"] == [
        {"model": tmp_settings.llm.model, "tokens_used": 150_000, "token_limit": tmp_settings.llm.limits.tpd}
    ]
    assert (
        budget["resets_at"] == "2026-10-23T00:00:00+00:00"
        and budget["resets_at_local"] == "23 Oct, 05:30 IST"
    )
    assert budget["exhausted"] is False


@pytest.fixture(autouse=True)
def _no_real_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test that forgets its fake launcher fails loudly instead of starting a real pipeline run."""

    def refuse(args: list[str]) -> None:
        raise AssertionError(f"a test tried to launch {args}")

    monkeypatch.setattr("pulse.web.jobs.launch_cli", refuse)
