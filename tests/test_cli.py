from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from pulse import cli
from pulse.config import Settings
from pulse.models import RunStatus
from pulse.pipeline import RunInProgress, RunOptions, RunOutcome, WeekNotComplete


class Captured:
    opts: RunOptions | None = None


def capture_run_options(  # craftsman-ignore: PY003 (hint on closing line)
    monkeypatch: pytest.MonkeyPatch, outcome: Any = None
) -> Captured:
    seen = Captured()

    def fake_run(settings: Settings, opts: RunOptions, **deps: object) -> RunOutcome:
        seen.opts = opts
        if isinstance(outcome, Exception):
            raise outcome
        return outcome or RunOutcome("2026-W42-x", RunStatus.INGESTED, Path("runs/2026-W42-x"))

    monkeypatch.setattr(cli, "run_pipeline", fake_run)
    monkeypatch.setattr(
        cli, "build_dependencies", lambda settings, **kwargs: cli.Deps(store=None, sources=[])
    )
    return seen


def invoke(tmp_settings: Settings, tmp_path: Path, *args: str) -> Any:
    config = tmp_path / "pulse.yaml"
    config.write_text(yaml.safe_dump(tmp_settings.model_dump(mode="json")), encoding="utf-8")
    return CliRunner().invoke(cli.app, ["--config", str(config), *args])


def test_cli_today_uses_ist_date(  # craftsman-ignore: PY003 (hint on closing line)
    monkeypatch: pytest.MonkeyPatch, tmp_settings: Settings, tmp_path: Path
) -> None:  # OPS-8
    monkeypatch.setattr(cli, "now_utc", lambda: datetime(2026, 10, 18, 19, 30, tzinfo=UTC))
    seen = capture_run_options(monkeypatch)
    result = invoke(tmp_settings, tmp_path, "ingest")
    assert result.exit_code == 0, result.output
    assert seen.opts is not None and seen.opts.today == date(2026, 10, 19)
    assert seen.opts.until_stage == "ingest" and seen.opts.started_at == datetime(
        2026, 10, 18, 19, 30, tzinfo=UTC
    )


def test_dry_run_flag_sets_until_validate(  # craftsman-ignore: PY003 (hint on closing line)
    monkeypatch: pytest.MonkeyPatch, tmp_settings: Settings, tmp_path: Path
) -> None:
    seen = capture_run_options(monkeypatch)
    invoke(tmp_settings, tmp_path, "run", "--week", "2026-W42", "--dry-run", "--force")
    assert seen.opts is not None
    assert (seen.opts.until_stage, seen.opts.week, seen.opts.force) == ("validate", "2026-W42", True)


def test_run_in_progress_exit_code(  # craftsman-ignore: PY003 (hint on closing line)
    monkeypatch: pytest.MonkeyPatch, tmp_settings: Settings, tmp_path: Path
) -> None:  # DEL-11
    capture_run_options(monkeypatch, RunInProgress("2026-W42"))
    result = invoke(tmp_settings, tmp_path, "ingest")
    assert result.exit_code == 3 and "run_in_progress" in result.output


def test_incomplete_week_exit_code(  # craftsman-ignore: PY003 (hint on closing line)
    monkeypatch: pytest.MonkeyPatch, tmp_settings: Settings, tmp_path: Path
) -> None:  # OPS-2
    capture_run_options(monkeypatch, WeekNotComplete("2026-W43 ends 2026-10-25"))
    result = invoke(tmp_settings, tmp_path, "ingest", "--week", "2026-W43")
    assert result.exit_code == 2 and "2026-W43" in result.output


def test_failed_run_exits_1(  # craftsman-ignore: PY003 (hint on closing line)
    monkeypatch: pytest.MonkeyPatch, tmp_settings: Settings, tmp_path: Path
) -> None:
    capture_run_options(monkeypatch, RunOutcome("2026-W42-x", RunStatus.FAILED, Path("runs/2026-W42-x")))
    result = invoke(tmp_settings, tmp_path, "ingest")
    assert result.exit_code == 1 and "FAILED" in result.output


def test_build_dependencies_uses_file_source(tmp_settings: Settings) -> None:
    deps = cli.build_dependencies(tmp_settings)
    assert [source.name for source in deps.sources] == ["file"]
    assert Path(tmp_settings.paths.db).is_file()


def test_rediscover_command_sets_flags(  # craftsman-ignore: PY003 (hint on closing line)
    monkeypatch: pytest.MonkeyPatch, tmp_settings: Settings, tmp_path: Path
) -> None:
    seen = capture_run_options(monkeypatch)
    result = invoke(tmp_settings, tmp_path, "rediscover", "--week", "2026-W42")
    assert result.exit_code == 0, result.output
    assert seen.opts is not None and (seen.opts.until_stage, seen.opts.rediscover) == ("discover", True)


def test_llm_stages_without_groq_key_exit_2(  # craftsman-ignore: PY003 (hint on closing line)
    monkeypatch: pytest.MonkeyPatch, tmp_settings: Settings, tmp_path: Path
) -> None:  # OPS-10
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(cli, "ENV_FILE", tmp_path / "absent.env")
    result = invoke(tmp_settings, tmp_path, "run", "--week", "2026-W42", "--until", "classify")
    assert result.exit_code == 2 and "GROQ_API_KEY" in result.output


def test_build_dependencies_adds_llm_only_when_asked(  # craftsman-ignore: PY003 (hint on closing line)
    monkeypatch: pytest.MonkeyPatch, tmp_settings: Settings
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_not_a_real_key")
    assert cli.build_dependencies(tmp_settings).llm is None
    assert cli.build_dependencies(tmp_settings, need_llm=True).llm is not None
