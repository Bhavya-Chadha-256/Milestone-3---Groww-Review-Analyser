"""What every dashboard request shares (Tasks 7.2–7.3): settings, the read-only store, jobs and hooks."""

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pulse.config import Settings
from pulse.store import Store
from pulse.timeutil import ISO_WEEK_PATTERN, Window, resolve_window, today_in_tz, window_for_week
from pulse.web.jobs import JobRunner, Process
from pulse.web.pulse_view import Readiness
from pulse.web.queries import Dataset, load_dataset

READINESS_TTL_SECONDS = 300  # the MCP check can take a minute on a sleeping server; don't repeat it per click


class InvalidRequest(Exception):
    """A bad parameter. The response names the fields but never echoes their values."""

    def __init__(self, fields: list[str]) -> None:
        super().__init__("invalid request")
        self.fields = sorted(set(fields))


class NotReady(Exception):
    pass


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ServerHooks:
    """Seams for tests: how CLI jobs start, how delivery readiness is checked, and the clock."""

    launch: Callable[[list[str]], Process] | None = None  # None: the real CLI, in a subprocess
    readiness: Callable[[], Readiness] | None = None  # None: pulse doctor's delivery checks
    now: Callable[[], datetime] = field(default=_utc_now)


def valid_week(week: str) -> bool:
    if not ISO_WEEK_PATTERN.match(week):
        return False
    try:
        window_for_week(week, 12, "UTC")
    except ValueError:
        return False
    return True


def allowed_hosts(settings: Settings) -> set[str]:
    """Host headers to answer: loopback locally, plus a public deployment's own names (Task 7.5)."""
    port, host = settings.dashboard.port, settings.dashboard.host
    local = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}", f"{host}:{port}"}
    public = {
        name
        for public_host in settings.dashboard.public_hosts
        for name in (public_host, f"{public_host}:{port}")
    }
    return local | public


class DashboardState:
    def __init__(
        self, settings: Settings, today: date | None, hooks: ServerHooks
    ) -> None:  # craftsman-ignore: WARN-PY001 (self + three)
        self.settings = settings
        self.today = today
        self.hooks = hooks
        self.jobs = JobRunner(hooks.launch, status_of=self.run_outcome)
        self._store: Store | None = None
        self._lock = threading.Lock()
        self._readiness: tuple[float, Readiness] | None = None

    def store(self) -> Store | None:
        """The read-only store, opened once the database exists; never created here."""
        with self._lock:
            if self._store is None and Path(self.settings.paths.db).is_file():
                self._store = Store.open_readonly(Path(self.settings.paths.db))
            return self._store

    def window(self, week: str | None) -> Window:
        window = self.settings.window
        if week:
            return window_for_week(week, window.weeks, window.timezone)
        return resolve_window(self.today or self._data_today(), window.weeks, window.timezone)

    def _data_today(self) -> date:
        """Today, unless the data stops earlier: then the day after the newest review, so the default week is
        the latest one the data covers (the snapshot may lag while the backfill catches up)."""
        timezone = self.settings.window.timezone
        today = today_in_tz(timezone, self.hooks.now())
        store = self.store()
        latest = store.latest_posted_at() if store else None
        if latest is None:
            return today
        return min(today, today_in_tz(timezone, latest) + timedelta(days=1))

    def dataset(self, week: str | None) -> Dataset:
        store = self.store()
        if store is None:
            raise NotReady
        return load_dataset(
            store,
            self.window(week),
            min_theme_reviews=self.settings.pulse.min_theme_reviews,
            trend_z=self.settings.themes.trend_z,
        )

    def readiness(self) -> Readiness:
        """Delivery readiness, cached for a few minutes; a failed check is re-run on the next request."""
        if self._readiness and time.monotonic() - self._readiness[0] < READINESS_TTL_SECONDS:
            return self._readiness[1]
        found = (self.hooks.readiness or self._doctor_readiness)()
        self._readiness = (time.monotonic(), found) if not found.problems else None
        return found

    def _doctor_readiness(self) -> Readiness:
        from pulse.cli import delivery_readiness  # the CLI module is heavy; load it only when asked

        return delivery_readiness(self.settings)

    def run_outcome(self, iso_week: str) -> tuple[str | None, list[str]] | None:
        """The week's latest run status and warning codes (codes and counts only, never review text)."""
        store = self.store()
        run = store.latest_run(iso_week) if store else None
        if run is None:
            return None
        report = Path(self.settings.paths.runs) / run["run_id"] / "report.json"
        warnings = (
            json.loads(report.read_text(encoding="utf-8")).get("warnings", []) if report.is_file() else []
        )
        return run["status"], warnings
