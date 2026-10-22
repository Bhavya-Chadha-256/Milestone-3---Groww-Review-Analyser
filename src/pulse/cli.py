"""`pulse` command-line interface: `fetch`, `raw status`, `run`, `ingest`, `eval` and `doctor`."""

import functools
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
import yaml
from pydantic import ValidationError

from pulse.acquire.app_store_feed import AppStoreFeedClient, FetchReport
from pulse.acquire.play_store import PlayStoreClient
from pulse.acquire.raw_export import raw_status, write_raw_export
from pulse.config import Settings, load_env, load_settings
from pulse.deliver.mcp_client import McpConfig, broken_gateway, gateway_problem, load_mcp_config, open_gateway
from pulse.evals.gold import GOLD_PATH, GoldMetaError, export_gold
from pulse.evals.runner import compare
from pulse.evals.suites.classification import run_classification_suite
from pulse.evals.suites.deterministic import run_deterministic_suite
from pulse.evals.suites.pulses import run_pulses_suite
from pulse.ingest.base import ReviewSource
from pulse.ingest.file_import import FileImportSource
from pulse.llm.client import GroqLLM
from pulse.llm.schema import LLMError
from pulse.models import RunStatus
from pulse.pipeline import (
    STAGES,
    STOPPING_STATUSES,
    MissingArtifact,
    RunInProgress,
    RunOptions,
    StageNotAvailable,
    WeekNotComplete,
    run_pipeline,
)
from pulse.store import Store
from pulse.timeutil import resolve_window, today_in_tz, window_bounds_utc

DEFAULT_CONFIG = Path("config/pulse.yaml")
ENV_FILE = Path(".env")
MCP_CONFIG = Path("config/mcp.yaml")
PLACEHOLDER = re.compile(r"^<.*>$")
DELIVERY_FIELDS = (("create_doc", "drive_folder_id"), ("append_doc", "doc_id"), ("create_draft", "recipient"))
EXIT_REFUSED = 2
SUITES = ("deterministic", "classification", "pulses")
EXIT_RUN_IN_PROGRESS = 3

app = typer.Typer(no_args_is_help=True, help="Groww Weekly Review Pulse.")
fetch_app = typer.Typer(no_args_is_help=True, help="Download raw reviews into data/raw/.")
raw_app = typer.Typer(no_args_is_help=True, help="Inspect raw export files in data/raw/.")
app.add_typer(fetch_app, name="fetch")  # craftsman-ignore: PY007 (typer command registration)
app.add_typer(raw_app, name="raw")  # craftsman-ignore: PY007 (typer command registration)


def now_utc() -> datetime:
    return datetime.now(UTC)


def make_feed_client(settings: Settings) -> AppStoreFeedClient:
    return AppStoreFeedClient(settings.product.app_store_id, storefront=settings.product.storefront)


def make_play_client(settings: Settings) -> PlayStoreClient:
    play = settings.sources.play_store
    return PlayStoreClient(
        settings.product.play_package,
        lang=play.lang,
        country=settings.product.storefront,
        max_reviews=play.max_reviews,
    )


@app.callback()
def main(  # craftsman-ignore: PY003 (return hint is on the closing line)
    ctx: typer.Context,
    config: Path = typer.Option(DEFAULT_CONFIG, "--config", help="Path to the settings YAML."),
) -> None:
    load_env(ENV_FILE)
    ctx.obj = config


def _settings(ctx: typer.Context) -> Settings:
    return load_settings(ctx.find_root().obj)


@fetch_app.command("app-store")
def fetch_app_store(  # craftsman-ignore: PY003 (return hint is on the closing line)
    ctx: typer.Context,
    weeks: int | None = typer.Option(None, min=1, help="Weeks to fetch (default: window.weeks)."),
) -> None:
    """Fetch App Store reviews from Apple's public feed and save them as a raw export."""
    settings = _settings(ctx)
    product = settings.product
    if not settings.sources.app_store.enabled:
        typer.echo("sources.app_store.enabled is false in the config; nothing fetched.")
        raise typer.Exit(2)
    if not product.app_store_id.isdigit():
        typer.echo(f"product.app_store_id must be a numeric App Store ID, got {product.app_store_id!r}.")
        raise typer.Exit(2)
    now = now_utc()
    since = _window_start(settings, now, weeks)
    reviews, report = make_feed_client(settings).fetch(since)
    _echo_fetch_summary(f"App Store app {product.app_store_id}", since, report)
    if not reviews:
        typer.echo("No reviews fetched; no file written. The feed is intermittent, so try again later.")
        raise typer.Exit(1)
    path = write_raw_export(reviews, Path(settings.paths.raw), "app_store_feed", now)
    typer.echo(f"wrote {path}")


@fetch_app.command("play-store")
def fetch_play_store(  # craftsman-ignore: PY003 (return hint is on the closing line)
    ctx: typer.Context,
    weeks: int | None = typer.Option(None, min=1, help="Weeks to fetch (default: window.weeks)."),
) -> None:
    """Fetch Play Store reviews with google-play-scraper and save them as a raw export."""
    settings = _settings(ctx)
    if not settings.sources.play_store.enabled:
        typer.echo("sources.play_store.enabled is false in the config; nothing fetched.")
        raise typer.Exit(2)
    now = now_utc()
    since = _window_start(settings, now, weeks)
    reviews, report = make_play_client(settings).fetch(since)
    _echo_fetch_summary(f"Play Store app {settings.product.play_package}", since, report)
    if not reviews:
        typer.echo("No reviews fetched; no file written.")
        raise typer.Exit(1)
    path = write_raw_export(reviews, Path(settings.paths.raw), "play_store_feed", now)
    typer.echo(f"wrote {path}")


def _window_start(settings: Settings, now: datetime, weeks: int | None) -> datetime:
    """IST start of the window ending with the last complete ISO week, as UTC."""
    timezone = settings.window.timezone
    window = resolve_window(today_in_tz(timezone, now), weeks or settings.window.weeks, timezone)
    return window_bounds_utc(window)[0]


def _echo_fetch_summary(label: str, since: datetime, report: FetchReport) -> None:
    span = (
        f"{report.oldest:%Y-%m-%d} → {report.newest:%Y-%m-%d} (UTC)"
        if report.oldest and report.newest
        else "no dates"
    )
    typer.echo(f"{label}: {report.reviews} reviews since {since:%Y-%m-%d %H:%M} UTC, {span}")
    typer.echo(
        f"pages: {report.pages_requested} requested, {report.pages_with_entries} with entries, "
        f"{report.pages_empty} empty, {report.retries} retries"
    )
    for warning in report.warnings:
        typer.echo(f"warning: {warning}")


@raw_app.command("status")
def raw_status_command(ctx: typer.Context) -> None:
    """List raw export files with their mapping, row count and date range."""
    settings = _settings(ctx)
    raw_dir = Path(settings.paths.raw)
    statuses = raw_status(raw_dir, Path(settings.paths.mappings), settings.window.timezone)
    if not statuses:
        typer.echo(
            f"No raw files in {raw_dir}. Run `pulse fetch app-store`, "
            "or add a Play export as generic__<name>.csv."
        )
        return
    for status in statuses:
        if status.problem:
            typer.echo(f"{status.name}  PROBLEM: {status.problem}")
        else:
            typer.echo(
                f"{status.name}  mapping={status.mapping}  rows={status.rows}  "
                f"{status.oldest} .. {status.newest}"
                + (f"  bad_dates={status.bad_dates}" if status.bad_dates else "")
            )
    if any(status.problem for status in statuses):
        raise typer.Exit(1)


@dataclass
class Deps:
    store: Any
    sources: list[ReviewSource] = field(default_factory=list)
    llm: Any = None
    gateway_factory: Any = None
    mcp_config: McpConfig | None = None


def build_dependencies(settings: Settings, *, need_llm: bool = False, need_gateway: bool = False) -> Deps:
    """The store and file source, the Groq client when a run reaches an LLM stage, and the MCP gateway
    factory when it reaches `publish` and config/mcp.yaml exists."""
    paths = settings.paths
    sources: list[ReviewSource] = []
    if settings.sources.file.enabled:
        sources.append(FileImportSource(Path(paths.raw), Path(paths.mappings)))
    store = Store(Path(paths.db))
    llm = GroqLLM(settings.llm, store) if need_llm else None  # raises LLMError when the key is missing
    factory, config = _gateway() if need_gateway else (None, None)
    return Deps(store=store, sources=sources, llm=llm, gateway_factory=factory, mcp_config=config)


def _gateway() -> tuple[Callable[[], Any], McpConfig | None]:
    """The factory that opens the configured MCP server, and its config. A missing or invalid
    config/mcp.yaml becomes a factory whose pre-check and doctor report the reason before any LLM spend,
    instead of a traceback (DEL-1)."""
    if not MCP_CONFIG.is_file():
        return broken_gateway(f"{MCP_CONFIG} not found (run from the project folder)"), None
    try:
        config = load_mcp_config(MCP_CONFIG)
    except (ValidationError, yaml.YAMLError) as error:
        return broken_gateway(f"{MCP_CONFIG} is invalid: {_config_problem(error)}"), None
    return functools.partial(open_gateway, config), config


def _config_problem(error: ValidationError | yaml.YAMLError) -> str:
    if isinstance(error, ValidationError):
        first = error.errors()[0]
        return f"{'.'.join(str(part) for part in first['loc'])}: {first['msg']}"
    return str(error).splitlines()[0]


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def doctor_checks(settings: Settings, deps: Deps) -> list[CheckResult]:
    """Everything a delivering run needs, checked without spending tokens (DEL-10, LLM-14, OPS-10)."""
    return [
        CheckResult("config", True, "config/pulse.yaml loads"),
        _delivery_check(settings, deps.mcp_config),
        _groq_check(settings, deps.llm),
        _mcp_check(deps.gateway_factory),
    ]


def _delivery_check(settings: Settings, config: McpConfig | None) -> CheckResult:
    """The `delivery` settings the mapped operations use must be real values (DEL-10)."""
    delivery = settings.delivery
    operations = set(config.operations) if config else {"create_doc", "create_draft"}
    needed = [name for operation, name in DELIVERY_FIELDS if operation in operations]
    unset = [name for name in needed if PLACEHOLDER.match(getattr(delivery, name))]
    if unset:
        return CheckResult(
            "delivery_config", False, f"still a <placeholder> in config/pulse.yaml: {', '.join(unset)}"
        )
    target = f"append to Doc {delivery.doc_id}" if "append_doc" in operations else "a Doc per week"
    draft = f"draft to {delivery.recipient}" if "create_draft" in operations else "no Gmail draft"
    return CheckResult("delivery_config", True, f"{target}; {draft}")


def _groq_check(settings: Settings, llm: Any) -> CheckResult:
    if llm is None:
        return CheckResult("groq_auth", False, f"{settings.llm.api_key_env} is not set; add it to .env")
    try:
        available = {model.id for model in llm.client.models.list().data}
    except Exception as error:  # a bad key or no network; the key itself is never printed
        return CheckResult("groq_auth", False, f"Groq refused the model list ({type(error).__name__})")
    missing = [model for model in (settings.llm.model, settings.judge_model) if model not in available]
    if missing:
        return CheckResult("groq_auth", False, f"models not available to this key: {', '.join(missing)}")
    return CheckResult("groq_auth", True, f"{settings.llm.model} and {settings.judge_model} available")


def _mcp_check(factory: Any) -> CheckResult:
    if factory is None:
        return CheckResult("mcp_tools", False, f"{MCP_CONFIG} not found")
    problem = gateway_problem(factory)
    return CheckResult("mcp_tools", problem is None, problem or "server reachable; every mapped tool exists")


@app.command("run")
def run_command(  # craftsman-ignore: PY003, WARN-PY001 (typer options; hint on closing line)
    ctx: typer.Context,
    week: str | None = typer.Option(None, "--week", help="ISO week to report, e.g. 2026-W42."),
    from_stage: str | None = typer.Option(None, "--from", help=f"Resume from a stage: {', '.join(STAGES)}."),
    until_stage: str = typer.Option("draft", "--until", help="Last stage to run."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Stop after validate; publish nothing."),
    force: bool = typer.Option(False, "--force", help="Run even above the cost guard."),
    rediscover: bool = typer.Option(False, "--rediscover", help="Rediscover the theme taxonomy."),
) -> None:
    """Run the weekly pipeline for the last complete ISO week (or --week)."""
    _execute(
        ctx, week, from_stage, "validate" if dry_run else until_stage, force=force, rediscover=rediscover
    )


@app.command("rediscover")
def rediscover_command(
    ctx: typer.Context,
    week: str | None = typer.Option(None, "--week", help="ISO week to report, e.g. 2026-W42."),
) -> None:
    """Discover a new theme taxonomy for the window (`run --until discover --rediscover`)."""
    _execute(ctx, week, None, "discover", rediscover=True)


@app.command("ingest")
def ingest_command(
    ctx: typer.Context,
    week: str | None = typer.Option(None, "--week", help="ISO week to report, e.g. 2026-W42."),
) -> None:
    """Import raw exports from data/raw/ into the review store (`run --until ingest`)."""
    _execute(ctx, week, None, "ingest")


def _execute(  # craftsman-ignore: WARN-PY001 (keyword flags forwarded from the commands)
    ctx: typer.Context, week: str | None, from_stage: str | None, until_stage: str, **flags: bool
) -> None:
    for stage in (from_stage, until_stage):
        if stage is not None and stage not in STAGES:
            typer.echo(f"unknown stage {stage!r}; stages are {', '.join(STAGES)}")
            raise typer.Exit(EXIT_REFUSED)
    settings = _settings(ctx)
    now = now_utc()
    opts = RunOptions(
        today=today_in_tz(settings.window.timezone, now),
        week=week,
        from_stage=from_stage,
        until_stage=until_stage,
        started_at=now,
        **flags,
    )
    try:
        deps = build_dependencies(
            settings,
            need_llm=STAGES.index(until_stage) >= STAGES.index("discover"),
            need_gateway=STAGES.index(until_stage) >= STAGES.index("publish"),
        )
    except LLMError as error:  # OPS-10
        typer.echo(str(error))
        raise typer.Exit(EXIT_REFUSED) from error
    try:
        outcome = run_pipeline(
            settings,
            opts,
            store=deps.store,
            sources=deps.sources,
            llm=deps.llm,
            gateway_factory=deps.gateway_factory,
        )
    except RunInProgress as error:
        typer.echo(f"run_in_progress: another run for {error} holds the lock")
        raise typer.Exit(EXIT_RUN_IN_PROGRESS) from error
    except (WeekNotComplete, StageNotAvailable, MissingArtifact) as error:
        label = "stage not available yet: " if isinstance(error, StageNotAvailable) else ""
        typer.echo(f"{label}{error}")
        raise typer.Exit(EXIT_REFUSED) from error
    typer.echo(f"run {outcome.run_id}: {outcome.status}")
    typer.echo(f"report: {outcome.run_dir / 'report.md'}")
    if RunStatus(outcome.status) in STOPPING_STATUSES:
        raise typer.Exit(1)


@app.command("eval")
def eval_command(  # craftsman-ignore: PY003, WARN-PY001 (typer options; hint on closing line)
    ctx: typer.Context,
    suite: str | None = typer.Option(
        None, "--suite", help="Suite to run: deterministic, classification or pulses."
    ),
    dataset: str = typer.Option("gold", "--dataset", help="gold or drift."),
    export_gold_count: int | None = typer.Option(
        None, "--export-gold", min=1, help="Export N reviews to label."
    ),
    compare_runs: tuple[Path, Path] | None = typer.Option(None, "--compare", help="Compare two result dirs."),
    gold: Path = typer.Option(GOLD_PATH, "--gold", help="Gold CSV path."),
) -> None:
    """Offline evals: export the gold set, run a suite, or compare two runs (evaluation.md)."""
    settings = _settings(ctx)
    if compare_runs:
        for row in compare(*compare_runs):
            typer.echo(f"{row.metric_id}: {row.a_value} → {row.b_value}  {row.verdict}")
        return
    if export_gold_count:
        store = Store(Path(settings.paths.db))
        taxonomy = store.latest_taxonomy()
        if taxonomy is None:
            typer.echo("No taxonomy yet: run `pulse run --until discover` first.")
            raise typer.Exit(EXIT_REFUSED)
        timezone = settings.window.timezone
        window = resolve_window(today_in_tz(timezone, now_utc()), settings.window.weeks, timezone)
        count = export_gold(store, window, taxonomy, n=export_gold_count, path=gold)
        typer.echo(f"wrote {count} reviews to label: {gold} (meta: {gold.with_suffix('.meta.json')})")
        return
    if suite not in SUITES:
        typer.echo(f"Use --suite {'|'.join(SUITES)}, --export-gold N or --compare A B.")
        raise typer.Exit(EXIT_REFUSED)
    try:
        out = _run_suite(settings, suite, dataset, gold)
    except (LLMError, GoldMetaError) as error:  # EvalAborted, no key, or an unusable gold set: nothing scored
        typer.echo(str(error))
        raise typer.Exit(EXIT_REFUSED) from error
    typer.echo(f"results: {out}")


def _run_suite(settings: Settings, suite: str, dataset: str, gold: Path) -> Path:
    """deterministic is free and needs no key; classification and pulses call Groq (evaluation §10)."""
    if suite == "deterministic":
        return run_deterministic_suite(settings)
    deps = build_dependencies(settings, need_llm=True)
    if suite == "pulses":
        return run_pulses_suite(settings, deps.llm)
    return run_classification_suite(
        settings, deps.llm, deps.store, dataset, gold_path=gold if dataset == "gold" else None
    )


@app.command("serve")
def serve_command(
    ctx: typer.Context,
    port: int | None = typer.Option(None, "--port", help="Port (default: dashboard.port, 8787)."),
) -> None:
    """Open the local, read-only dashboard in your browser (needs: uv sync --extra web)."""
    settings = _settings(ctx)
    try:
        import uvicorn

        from pulse.web.app import create_app
    except ImportError:
        typer.echo("The dashboard needs the web extra; install with: uv sync --extra web")
        raise typer.Exit(EXIT_REFUSED) from None
    dashboard = settings.dashboard.model_copy(update={"port": port or settings.dashboard.port})
    settings = settings.model_copy(update={"dashboard": dashboard})
    typer.echo(f"Review Pulse dashboard: http://{dashboard.host}:{dashboard.port}  (Ctrl+C to stop)")
    # No access log and quiet server logs: query strings and review text never reach a log line (E-WEB-3).
    uvicorn.run(
        create_app(settings), host=dashboard.host, port=dashboard.port, access_log=False, log_level="warning"
    )


@app.command("doctor")
def doctor_command(ctx: typer.Context) -> None:
    """Check config, the Groq key and models, and the Workspace MCP server before a delivering run."""
    settings = _settings(ctx)
    try:
        deps = build_dependencies(settings, need_llm=True, need_gateway=True)
    except LLMError:
        deps = build_dependencies(settings, need_gateway=True)  # the groq_auth check reports the missing key
    checks = doctor_checks(settings, deps)
    for check in checks:
        typer.echo(f"{check.name} {'OK' if check.ok else 'FAIL'}  {check.detail}")
    if not all(check.ok for check in checks):
        raise typer.Exit(1)
