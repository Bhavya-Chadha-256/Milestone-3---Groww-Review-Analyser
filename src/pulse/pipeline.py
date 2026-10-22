"""The resumable run skeleton: windows, run IDs, the per-week lock and the stage registry (§2).

Each phase registers its own stages with `register_stage` (Phase 2: ingest, in pulse.stages.ingest).
"""

import fcntl
import importlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from pulse.config import Settings
from pulse.ingest.base import ReviewSource
from pulse.models import RunStatus
from pulse.report.run_report import RunReport
from pulse.store import Store
from pulse.timeutil import Window, resolve_window, window_for_week

STAGES = ("ingest", "discover", "classify", "aggregate", "quotes", "compose", "validate", "publish", "draft")
STOPPING_STATUSES = frozenset({RunStatus.FAILED, RunStatus.INSUFFICIENT_DATA, RunStatus.VALIDATION_FAILED})
STAGE_MODULES = ("pulse.stages.ingest", "pulse.stages.themes")


class MissingArtifact(Exception):
    pass


class StageNotAvailable(Exception):
    pass


class RunInProgress(Exception):
    pass


class WeekNotComplete(Exception):
    pass


@dataclass(frozen=True)
class RunOptions:
    today: date  # the IST date (OPS-8)
    week: str | None = None
    from_stage: str | None = None
    until_stage: str = "draft"
    force: bool = False
    rediscover: bool = False
    started_at: datetime | None = None  # names the run; defaults to now


@dataclass(frozen=True)
class RunOutcome:
    run_id: str
    status: RunStatus
    run_dir: Path


@dataclass
class RunContext:
    settings: Settings
    opts: RunOptions
    store: Store
    sources: list[ReviewSource]
    run_id: str
    run_dir: Path
    window: Window
    llm: Any = None
    gateway_factory: Any = None
    report: RunReport = field(default_factory=RunReport)
    state: dict[str, Any] = field(default_factory=dict)
    stage: str = "ingest"

    def save_artifact(self, name: str, data: Any) -> Path:
        path = self.run_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, BaseModel):
            text = data.model_dump_json(indent=2)
        else:
            text = json.dumps(data, indent=2, default=str, ensure_ascii=False)
        path.write_text(text + "\n", encoding="utf-8")
        return path

    def load_artifact(self, name: str, model: type[BaseModel] | None = None) -> Any:
        path = self.run_dir / name
        if not path.is_file():
            raise MissingArtifact(f"cannot resume from {self.stage}: missing {name} in {self.run_dir}")
        text = path.read_text(encoding="utf-8")
        return model.model_validate_json(text) if model else json.loads(text)


StageFunc = Callable[[RunContext], RunStatus | None]
STAGE_FUNCS: dict[str, StageFunc] = {}


def register_stage(name: str) -> Callable[[StageFunc], StageFunc]:
    if name not in STAGES:
        raise ValueError(f"unknown stage {name!r}")

    def decorate(func: StageFunc) -> StageFunc:
        STAGE_FUNCS[name] = func
        return func

    return decorate


@contextmanager
def run_lock(runs_dir: Path, iso_week: str) -> Iterator[None]:
    """One run per ISO week at a time (DEL-11); a second concurrent run raises RunInProgress."""
    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    with (runs_dir / f".lock-{iso_week}").open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RunInProgress(iso_week) from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def resolve_run_window(settings: Settings, opts: RunOptions) -> Window:
    weeks, timezone = settings.window.weeks, settings.window.timezone
    window = (
        window_for_week(opts.week, weeks, timezone)
        if opts.week
        else resolve_window(opts.today, weeks, timezone)
    )
    if window.end >= opts.today:
        raise WeekNotComplete(
            f"{window.iso_week} ends {window.end}; it can be run from {window.end} + 1 day (IST)"
        )
    return window


def stage_range(opts: RunOptions) -> list[str]:
    first = STAGES.index(opts.from_stage or "ingest")
    last = STAGES.index(opts.until_stage)
    return list(STAGES[first : last + 1])


def run_pipeline(  # craftsman-ignore: PY003, WARN-PY001 (plan interface; hint on closing line)
    settings: Settings,
    opts: RunOptions,
    *,
    store: Store,
    sources: list[ReviewSource],
    llm: Any = None,
    gateway_factory: Any = None,
) -> RunOutcome:
    for module in STAGE_MODULES:
        importlib.import_module(module)
    window = resolve_run_window(settings, opts)
    stages = stage_range(opts)
    missing = [stage for stage in stages if stage not in STAGE_FUNCS]
    if missing:
        raise StageNotAvailable(missing[0])
    with run_lock(Path(settings.paths.runs), window.iso_week):
        run_id = _run_id_for(settings, opts, store, window)
        context = RunContext(
            settings=settings,
            opts=opts,
            store=store,
            sources=sources,
            run_id=run_id,
            run_dir=Path(settings.paths.runs) / run_id,
            window=window,
            llm=llm,
            gateway_factory=gateway_factory,
        )
        if opts.from_stage:  # OPS-11: keep the sections earlier stages wrote
            context.report = RunReport.load_state(context.run_dir / "report.json")
        status = _run_stages(context, stages)
        return RunOutcome(run_id, status, context.run_dir)


def _run_id_for(settings: Settings, opts: RunOptions, store: Store, window: Window) -> str:
    if opts.from_stage:
        earlier = store.latest_run(window.iso_week)
        if earlier is None:
            raise MissingArtifact(
                f"cannot resume from {opts.from_stage}: no earlier run for {window.iso_week}"
            )
        return earlier["run_id"]
    started = (opts.started_at or datetime.now(UTC)).astimezone(UTC)
    run_id = f"{window.iso_week}-{started:%Y%m%dT%H%M%S}"
    store.create_run(run_id, window)
    return run_id


def _run_stages(context: RunContext, stages: list[str]) -> RunStatus:
    status = RunStatus.FAILED
    try:
        for stage in stages:
            context.stage = stage
            status = STAGE_FUNCS[stage](context) or status
            if status in STOPPING_STATUSES:
                break
    except Exception:
        status = RunStatus.FAILED
        context.report.warnings.append(f"stage_failed:{context.stage}")
        raise
    finally:
        context.report.write(context.run_dir / "report.md", f"Run report — {context.run_id}")
        context.report.save_state(context.run_dir / "report.json")
        context.store.set_run_status(context.run_id, status, **context.report.counts)
    return status
