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


def test_eval_export_gold_writes_csv(  # craftsman-ignore: PY003 (hint on closing line)
    monkeypatch: pytest.MonkeyPatch, tmp_settings: Settings, tmp_path: Path
) -> None:
    from factories import make_review

    from pulse.models import Taxonomy, Theme
    from pulse.store import Store

    store = Store(Path(tmp_settings.paths.db))
    store.upsert_reviews([make_review(review_id=f"g{index:03d}") for index in range(30)])
    themes = [
        Theme(id=slug, name=slug, definition=slug, include_hints=[], exclude_hints=[])
        for slug in ("a", "b", "c")
    ]
    store.save_taxonomy(
        Taxonomy(
            version=1,
            themes=themes,
            created_from_run="r",
            created_at=datetime(2026, 10, 1, tzinfo=UTC),
            sample_review_ids=[],
        )
    )
    monkeypatch.setattr(cli, "now_utc", lambda: datetime(2026, 10, 19, 5, 0, tzinfo=UTC))
    gold = tmp_path / "gold" / "gold_v1.csv"
    result = invoke(tmp_settings, tmp_path, "eval", "--export-gold", "20", "--gold", str(gold))
    assert result.exit_code == 0, result.output
    assert "20" in result.output and gold.is_file() and gold.with_suffix(".meta.json").is_file()


def test_eval_compare_prints_verdicts(tmp_settings: Settings, tmp_path: Path) -> None:
    from pulse.config import Pricing
    from pulse.evals.runner import EvalRun
    from pulse.llm.schema import UsageLedger

    paths = []
    for name in ("a", "b"):
        run = EvalRun("classification", tmp_path / name, started=datetime(2026, 10, 22, 9, tzinfo=UTC))
        for index in range(20):
            run.case(f"r{index}", {"gold": "x", "pred": "x"}, served_model="m", primary_model="m")
        run.metric("E-CLS-1", 1.0, target=0.8, gate="blocking")
        paths.append(run.finish(UsageLedger(), Pricing()))
    result = invoke(tmp_settings, tmp_path, "eval", "--compare", str(paths[0]), str(paths[1]))
    assert result.exit_code == 0, result.output
    assert "E-CLS-1" in result.output and "within noise" in result.output


def test_eval_suite_aborted_exits_with_message(
    tmp_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # review I3
    from pulse.evals.suites.classification import EvalAborted

    def out_of_budget(*args: Any, **kwargs: Any) -> Path:
        raise EvalAborted("daily token budget ran out; nothing was scored. Run again after 00:00 UTC")

    monkeypatch.setattr(
        cli, "build_dependencies", lambda settings, **kwargs: cli.Deps(store=None, sources=[])
    )
    monkeypatch.setattr(cli, "run_classification_suite", out_of_budget)
    result = invoke(tmp_settings, tmp_path, "eval", "--suite", "classification")
    assert result.exit_code == 2 and "nothing was scored" in result.output


def test_eval_refuses_unusable_gold_set_with_message(
    tmp_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # review I6
    from pulse.evals.gold import GoldMetaError

    def bad_gold(*args: Any, **kwargs: Any) -> Path:
        raise GoldMetaError("2 gold_theme values are not classes of taxonomy v1: kyc")

    monkeypatch.setattr(
        cli, "build_dependencies", lambda settings, **kwargs: cli.Deps(store=None, sources=[])
    )
    monkeypatch.setattr(cli, "run_classification_suite", bad_gold)
    result = invoke(tmp_settings, tmp_path, "eval", "--suite", "classification")
    assert result.exit_code == 2 and "not classes of taxonomy v1" in result.output


def test_eval_runs_deterministic_and_pulses_suites(
    tmp_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # Task 4.5
    ran: list[str] = []

    def deterministic(settings: Settings) -> Path:
        ran.append("deterministic")
        return tmp_path / "det"

    def pulses(settings: Settings, llm: Any) -> Path:
        ran.append("pulses")
        return tmp_path / "pul"

    built: list[bool] = []

    def deps(settings: Settings, **kwargs: Any) -> Any:
        built.append(kwargs.get("need_llm", False))
        return cli.Deps(store=None, sources=[], llm="llm")

    monkeypatch.setattr(cli, "run_deterministic_suite", deterministic)
    monkeypatch.setattr(cli, "run_pulses_suite", pulses)
    monkeypatch.setattr(cli, "build_dependencies", deps)
    first = invoke(tmp_settings, tmp_path, "eval", "--suite", "deterministic")
    second = invoke(tmp_settings, tmp_path, "eval", "--suite", "pulses")
    assert first.exit_code == 0 and second.exit_code == 0, first.output + second.output
    assert ran == ["deterministic", "pulses"] and built == [True]
    assert "det" in first.output and "pul" in second.output
