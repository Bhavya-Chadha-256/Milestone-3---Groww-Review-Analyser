"""The dashboard's read-only HTTP API (Task 7.2, architecture.md §16).

Local only: `pulse serve` binds a loopback address, every request with a foreign `Host` header is refused
(DNS rebinding), and the database is opened read-only. Nothing about a request is logged: there is no
access log, and query strings and review text never reach a log line (E-WEB-3).
"""

import csv
import io
import threading
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from pulse.analysis.aggregate import trend_arrow
from pulse.config import Settings
from pulse.store import Store
from pulse.timeutil import (
    ISO_WEEK_PATTERN,
    Window,
    iso_week_label,
    resolve_window,
    today_in_tz,
    window_for_week,
)
from pulse.web.queries import (
    REVIEW_FIELDS,
    SORTS,
    Dataset,
    ReviewFilter,
    data_quality,
    load_dataset,
    rating_distribution,
    review_page,
    summary,
    theme_breakdown,
    weekly_trend,
)

LIST_FIELDS = frozenset({"sources", "ratings", "themes", "sentiments", "languages", "app_versions"})
PAGE_PARAMS = frozenset({"week", "sort", "page", "page_size"})
FORMULA_PREFIXES = ("=", "+", "-", "@")
NOT_READY = "No review database yet. Run `pulse ingest` first."


class InvalidRequest(Exception):
    """A bad query parameter. The response names the fields but never echoes their values."""

    def __init__(self, fields: list[str]) -> None:
        super().__init__("invalid request")
        self.fields = sorted(set(fields))


class NotReady(Exception):
    pass


class DashboardState:
    """What every request shares: the settings and the read-only store, opened once the database exists."""

    def __init__(self, settings: Settings, today: date | None) -> None:
        self.settings = settings
        self.today = today
        self._store: Store | None = None
        self._lock = threading.Lock()

    def store(self) -> Store | None:
        with self._lock:
            if self._store is None and Path(self.settings.paths.db).is_file():
                self._store = Store.open_readonly(Path(self.settings.paths.db))
            return self._store

    def window(self, week: str | None) -> Window:
        window = self.settings.window
        if week:
            return window_for_week(week, window.weeks, window.timezone)
        today = self.today or today_in_tz(window.timezone, datetime.now(UTC))
        return resolve_window(today, window.weeks, window.timezone)

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


def create_app(settings: Settings, *, today: date | None = None) -> FastAPI:
    """The dashboard API. `today` pins the default week (tests); otherwise it is today's date in IST."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    state = DashboardState(settings, today)
    app.state.dashboard = state
    _install_guards(app, settings)
    app.include_router(router)
    return app


def _install_guards(app: FastAPI, settings: Settings) -> None:
    port = settings.dashboard.port
    host = settings.dashboard.host
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}", f"{host}:{port}"}

    @app.middleware("http")
    async def guard(request: Request, call_next: Any) -> Response:
        if request.headers.get("host") not in allowed:  # DNS rebinding
            return JSONResponse({"error": "host not allowed"}, status_code=400)
        response = await call_next(request)
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(InvalidRequest)
    async def invalid(request: Request, error: InvalidRequest) -> JSONResponse:
        return JSONResponse({"error": "invalid request", "fields": error.fields}, status_code=422)

    @app.exception_handler(NotReady)
    async def not_ready(request: Request, error: NotReady) -> JSONResponse:
        return JSONResponse({"error": NOT_READY}, status_code=409)


def parse_filter(request: Request) -> tuple[str | None, ReviewFilter]:
    """The week and the filter from the query string; repeated parameters fill the list fields."""
    params = request.query_params
    bad = [name for name in params if name not in ReviewFilter.model_fields and name not in PAGE_PARAMS]
    data: dict[str, Any] = {}
    for name in ReviewFilter.model_fields:
        values = params.getlist(name)
        if values:
            data[name] = values if name in LIST_FIELDS else values[-1]
    flt = ReviewFilter()
    try:
        flt = ReviewFilter.model_validate(data)
    except ValidationError as error:
        bad += [str(detail["loc"][0]) for detail in error.errors()]
    week = params.get("week") or None
    if week is not None and not _valid_week(week):
        bad.append("week")
    if bad:
        raise InvalidRequest(bad)
    return week, flt


def _valid_week(week: str) -> bool:
    if not ISO_WEEK_PATTERN.match(week):
        return False
    try:
        window_for_week(week, 12, "UTC")
    except ValueError:
        return False
    return True


def _paging(request: Request, settings: Settings) -> tuple[str, int, int]:
    params = request.query_params
    sort = params.get("sort", "newest")
    page = _positive(params.get("page", "1"))
    size = _positive(params.get("page_size", str(settings.dashboard.page_size)))
    bad = [name for name, ok in (("sort", sort in SORTS), ("page", page), ("page_size", size)) if not ok]
    if bad:
        raise InvalidRequest(bad)
    return sort, page or 1, min(size or 1, settings.dashboard.max_page_size)


def _positive(value: str) -> int | None:
    return int(value) if value.isdigit() and int(value) > 0 else None


router = APIRouter(prefix="/api")


def dashboard(request: Request) -> DashboardState:
    return request.app.state.dashboard


@router.get("/meta")
def meta_route(request: Request) -> dict[str, Any]:
    week, _ = parse_filter(request)
    state = dashboard(request)
    if state.store() is None:
        return {"ready": False}
    return _meta(state.dataset(week), state.settings)


@router.get("/summary")
def summary_route(request: Request) -> dict[str, Any]:
    week, flt = parse_filter(request)
    return summary(dashboard(request).dataset(week), flt)


@router.get("/themes")
def themes_route(request: Request) -> list[dict[str, Any]]:
    week, flt = parse_filter(request)
    return [
        asdict(stats) | {"arrow": trend_arrow(stats.trend_pts, stats.trend_significant)}
        for stats in theme_breakdown(dashboard(request).dataset(week), flt)
    ]


@router.get("/trend")
def trend_route(request: Request) -> list[dict[str, Any]]:
    week, flt = parse_filter(request)
    return weekly_trend(dashboard(request).dataset(week), flt)


@router.get("/ratings")
def ratings_route(request: Request) -> dict[str, dict[int, int]]:
    week, flt = parse_filter(request)
    return rating_distribution(dashboard(request).dataset(week), flt)


@router.get("/reviews")
def reviews_route(request: Request) -> dict[str, Any]:
    week, flt = parse_filter(request)
    state = dashboard(request)
    sort, page, size = _paging(request, state.settings)
    return review_page(state.dataset(week), flt, sort, page, size)


@router.get("/reviews.csv")
def export_route(request: Request) -> Response:
    state = dashboard(request)
    limits = state.settings.dashboard
    if not limits.allow_export:
        return JSONResponse({"error": "export is turned off (dashboard.allow_export)"}, status_code=403)
    week, flt = parse_filter(request)
    sort, _, _ = _paging(request, state.settings)
    dataset = state.dataset(week)
    rows = review_page(dataset, flt, sort, 1, limits.export_max_rows)["items"]
    start, end = flt.start or dataset.window.start, flt.end or dataset.window.end
    return Response(
        _csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="reviews-{start}-{end}.csv"'},
    )


@router.get("/quality")
def quality_route(request: Request) -> dict[str, Any]:
    week, flt = parse_filter(request)
    return data_quality(dashboard(request).dataset(week), flt)


def _meta(dataset: Dataset, settings: Settings) -> dict[str, Any]:
    window = dataset.window
    in_window = [
        review
        for review in dataset.reviews
        if window.start <= dataset.local_dates[review.review_id] <= window.end
    ]
    weeks = {iso_week_label(day) for day in dataset.local_dates.values() if day <= window.end}
    themes = dataset.taxonomy.themes if dataset.taxonomy else []
    versions = {review.app_version for review in dataset.reviews if review.app_version}
    return {
        "ready": True,
        "week": window.iso_week,
        "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "weeks": sorted(weeks | {window.iso_week}, reverse=True),
        "themes": [{"id": theme.id, "name": theme.name} for theme in themes],
        "app_versions": sorted(versions, key=_version_key, reverse=True),
        "languages": sorted({review.language for review in dataset.reviews}),
        "total": len(in_window),
        "eligible": sum(review.excluded_reason is None for review in in_window),
        "page_size": settings.dashboard.page_size,
        "allow_export": settings.dashboard.allow_export,
    }


def _version_key(version: str) -> tuple[tuple[int, str], ...]:
    return tuple((int(part), "") if part.isdigit() else (-1, part) for part in version.split("."))


def _csv(rows: list[dict[str, Any]]) -> str:
    """The review rows as CSV; a cell that a spreadsheet would run as a formula gets a leading quote."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(REVIEW_FIELDS)
    for row in rows:
        writer.writerow([_cell(row[name]) for name in REVIEW_FIELDS])
    return buffer.getvalue()


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(FORMULA_PREFIXES) else text
