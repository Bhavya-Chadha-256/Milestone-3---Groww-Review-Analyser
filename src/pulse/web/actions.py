"""The Weekly pulse tab's endpoints (Task 7.3): the pulse view, run jobs, the token budget and readiness.

Every POST must come from the dashboard page itself: it carries `X-Pulse-Client: dashboard`, and its
`Origin`, when present, is the server's own (CSRF from other sites gets a 403). Publish and Draft also
wait for the delivery half of `pulse doctor`; the pipeline's own gates still apply after that.
"""

from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from pulse.web.jobs import ACTIONS, JobBusy
from pulse.web.pulse_view import pulse_view
from pulse.web.state import DashboardState, InvalidRequest, NotReady, allowed_hosts, valid_week

router = APIRouter(prefix="/api")
DELIVERY_ACTIONS = frozenset({"publish", "draft"})
NO_DRAFT_TOOL = "Your MCP server has no Gmail draft tool"


def dashboard(request: Request) -> DashboardState:
    return request.app.state.dashboard


@router.get("/pulse")
def pulse_route(request: Request) -> Any:
    state = dashboard(request)
    week = request.query_params.get("week") or state.window(None).iso_week
    if not valid_week(week):
        raise InvalidRequest(["week"])
    store = state.store()
    if store is None:
        raise NotReady
    view = pulse_view(store, state.settings, week)
    return view if view is not None else JSONResponse({"empty": True}, status_code=404)


@router.post("/jobs")
async def start_job(request: Request) -> JSONResponse:
    state = dashboard(request)
    if not _from_dashboard(request, state):
        return JSONResponse({"error": "requests must come from the dashboard page"}, status_code=403)
    action, week = await _job_body(request)
    if action in DELIVERY_ACTIONS:
        blocked = await run_in_threadpool(_delivery_block, state, action)
        if blocked:
            return JSONResponse({"error": blocked}, status_code=409)
    try:
        job = state.jobs.start(action, week)
    except JobBusy:
        return JSONResponse({"error": "another job is still running"}, status_code=409)
    return JSONResponse(job.public(), status_code=202)


@router.get("/jobs/{job_id}")
def job_route(request: Request, job_id: str) -> Any:
    job = dashboard(request).jobs.get(job_id)
    return job.public() if job else JSONResponse({"error": "no such job"}, status_code=404)


@router.get("/llm-budget")
def budget_route(request: Request) -> dict[str, Any]:
    """Today's token use per pipeline model, and the next reset (00:00 UTC) in the local timezone."""
    state = dashboard(request)
    llm, now = state.settings.llm, state.hooks.now().astimezone(UTC)
    store = state.store()
    used, _ = store.llm_usage(now.date().isoformat(), llm.model) if store else (0, 0)
    reset = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=UTC)
    local = reset.astimezone(ZoneInfo(state.settings.window.timezone))
    return {
        "models": [{"model": llm.model, "tokens_used": used, "token_limit": llm.limits.tpd}],
        "exhausted": used >= llm.limits.tpd,
        "resets_at": reset.isoformat(),
        "resets_at_local": f"{local.day} {local:%b, %H:%M %Z}",
    }


@router.get("/delivery-ready")
def delivery_ready_route(request: Request) -> dict[str, Any]:
    found = dashboard(request).readiness()
    return {
        "ready": not found.problems,
        "problems": [{"name": name, "detail": detail} for name, detail in found.problems],
        "draft_supported": found.draft_supported,
    }


def _from_dashboard(request: Request, state: DashboardState) -> bool:
    origin = request.headers.get("origin")
    same_origin = origin is None or origin in {f"http://{host}" for host in allowed_hosts(state.settings)}
    return request.headers.get("x-pulse-client") == "dashboard" and same_origin


async def _job_body(request: Request) -> tuple[str, str]:
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise InvalidRequest(["body"])
    action, week = body.get("action"), body.get("week")
    bad = [
        name
        for name, ok in (("action", action in ACTIONS), ("week", isinstance(week, str) and valid_week(week)))
        if not ok
    ]
    if bad:
        raise InvalidRequest(bad)
    return action, week


def _delivery_block(state: DashboardState, action: str) -> str | None:
    """Why Publish or Draft can't start yet, or None. Runs `pulse doctor`'s delivery checks (cached)."""
    found = state.readiness()
    if found.problems:
        return "Delivery isn't set up: " + "; ".join(f"{name}: {detail}" for name, detail in found.problems)
    if action == "draft" and not found.draft_supported:
        return NO_DRAFT_TOOL
    return None
