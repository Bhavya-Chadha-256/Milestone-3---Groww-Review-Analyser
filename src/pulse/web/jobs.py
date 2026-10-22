"""Run actions from the dashboard (Task 7.3): each one launches the existing CLI as a subprocess.

So the pipeline's lock, validation gates and MCP allowlist apply exactly as on the command line. The
arguments come only from `ACTIONS`; nothing goes through a shell, and the process's output is discarded,
never shown (it can carry review text).
"""

import itertools
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Protocol

ACTIONS: dict[str, tuple[str, ...]] = {
    "generate": ("run", "--week", "{week}", "--dry-run"),
    "publish": ("run", "--week", "{week}", "--from", "publish", "--until", "publish"),
    "draft": ("run", "--week", "{week}", "--from", "draft"),
}
EXIT_RUN_IN_PROGRESS = 3  # pulse run: another run holds the week's lock (DEL-11)


class Process(Protocol):
    def poll(self) -> int | None: ...


class JobBusy(Exception):
    pass


@dataclass
class Job:
    id: str
    action: str
    iso_week: str
    state: str  # running, succeeded, failed or locked
    exit_code: int | None = None
    run_status: str | None = None
    warnings: list[str] | None = None

    def public(self) -> dict[str, Any]:
        return asdict(self)


def launch_cli(args: list[str]) -> subprocess.Popen:
    """Start the CLI with no shell; its output is dropped so nothing it prints can reach the page."""
    return subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL
    )


class JobRunner:
    """At most one job at a time. `status_of(week)` reads the week's run status and warnings afterwards."""

    def __init__(
        self,
        launch: Callable[[list[str]], Process] | None,
        status_of: Callable[[str], tuple[str | None, list[str]] | None],
    ) -> None:
        self._launch = launch
        self._status_of = status_of
        self._jobs: dict[str, tuple[Job, Process]] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def start(self, action: str, iso_week: str) -> Job:
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        with self._lock:
            if any(self._refresh(job, process).state == "running" for job, process in self._jobs.values()):
                raise JobBusy("a job is already running")
            args = [
                sys.executable,
                "-m",
                "pulse.cli",
                *(part.format(week=iso_week) for part in ACTIONS[action]),
            ]
            process = (self._launch or launch_cli)(args)
            job = Job(id=f"job{next(self._ids)}", action=action, iso_week=iso_week, state="running")
            self._jobs[job.id] = (job, process)
            return self._refresh(job, process)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            found = self._jobs.get(job_id)
            return self._refresh(*found) if found else None

    def _refresh(self, job: Job, process: Process) -> Job:
        if job.state != "running":
            return job
        code = process.poll()
        if code is None:
            return job
        job.exit_code = code
        job.state = {0: "succeeded", EXIT_RUN_IN_PROGRESS: "locked"}.get(code, "failed")
        outcome = self._status_of(job.iso_week)
        if outcome is not None:
            job.run_status, job.warnings = outcome
        return job
