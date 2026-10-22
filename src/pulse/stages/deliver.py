"""The publish and draft stages: the validated pulse goes to one weekly Doc and one Gmail draft (Task 5.3).

Delivery only ever runs on a pulse whose saved `validation.json` passed, and only through the MCP gateway.
A tool failure names the re-authorise step and the exact `--from` command (DEL-4).
"""

from collections.abc import Awaitable, Callable
from html import escape
from typing import Any

from pulse.deliver.docs import publish_doc
from pulse.deliver.gmail import create_draft
from pulse.deliver.mcp_client import ToolCallFailed, WorkspaceGateway, run_with_gateway
from pulse.models import Delivery, RunStatus
from pulse.pipeline import RunContext, register_stage
from pulse.report.render import render_html, render_text
from pulse.stages.pulse import pulse_context
from pulse.timeutil import Window

TOOL_FAILURE = (
    "MCP tool call failed: {error}. If this is an auth error, re-authorise the Workspace MCP server, "
    "then run: pulse run --week {week} --from {stage}"
)
SUBJECT = "Groww Review Pulse — {iso_week}"
Operation = Callable[[WorkspaceGateway], Awaitable[Any]]


@register_stage("publish")
def publish_stage(context: RunContext) -> RunStatus:
    if not context.load_artifact("validation.json").get("passed"):
        context.report.sections["Delivery"] = "Not published: the saved pulse did not pass validation."
        return RunStatus.VALIDATION_FAILED
    if context.gateway_factory is None:
        return _no_gateway(context)
    try:
        delivery, warnings = run_with_gateway(context.gateway_factory, _publish_operation(context))
    except ToolCallFailed as error:
        return _tool_failure(context, error, "publish", RunStatus.FAILED)
    context.report.warnings.extend(warnings)
    _record(context, delivery)
    return RunStatus.PUBLISHED_DOC_ONLY


@register_stage("draft")
def draft_stage(context: RunContext) -> RunStatus:
    if not context.load_artifact("validation.json").get("passed"):
        context.report.sections["Delivery"] = "No draft: the saved pulse did not pass validation."
        return RunStatus.VALIDATION_FAILED
    delivery = context.store.get_delivery(context.window.iso_week)
    if delivery is None or not delivery.doc_id or not (context.run_dir / "delivery.json").is_file():
        context.report.sections["Delivery"] = "This run hasn't published the Doc; run --from publish first."
        return RunStatus.FAILED
    if context.gateway_factory is None:
        return _no_gateway(context)
    try:
        delivery = run_with_gateway(
            context.gateway_factory, _draft_operation(context, delivery.doc_url or "")
        )
    except ToolCallFailed as error:  # the Doc is written; --from draft finishes the job (DEL-8)
        return _tool_failure(context, error, "draft", RunStatus.PUBLISHED_DOC_ONLY)
    _record(context, delivery)
    return RunStatus.DELIVERED if delivery.doc_verified else RunStatus.DELIVERY_UNVERIFIED


def _publish_operation(context: RunContext) -> Operation:
    ctx, settings = pulse_context(context), context.settings.delivery
    week = context.window.iso_week

    def publish(gateway: WorkspaceGateway) -> Awaitable[tuple[Delivery, list[str]]]:
        return publish_doc(
            gateway,
            context.store,
            iso_week=week,
            title=settings.doc_title.format(iso_week=week, range=week_range(context.window)),
            body=render_text(ctx),  # plain text: a Doc shows Markdown syntax literally
            folder_id=settings.drive_folder_id,
            must_contain=[theme.name for theme in ctx.themes] + [quote.text for quote in ctx.quotes],
        )

    return publish


def _draft_operation(context: RunContext, url: str) -> Operation:
    ctx, week = pulse_context(context), context.window.iso_week

    def draft(gateway: WorkspaceGateway) -> Awaitable[Delivery]:
        return create_draft(
            gateway,
            context.store,
            iso_week=week,
            recipient=context.settings.delivery.recipient,
            subject=SUBJECT.format(iso_week=week),
            text=f"{render_text(ctx)}\n\nFull pulse: {url}",
            html=f'{render_html(ctx)}<p>Full pulse: <a href="{escape(url)}">{escape(url)}</a></p>\n',
            force=context.opts.force,
        )

    return draft


def week_range(window: Window) -> str:
    """'12–18 Oct', or '28 Sep – 4 Oct' when the reported week spans two months."""
    start, end = window.latest_week_start, window.end
    if start.month == end.month:
        return f"{start.day}–{end.day} {end:%b}"
    return f"{start.day} {start:%b} – {end.day} {end:%b}"


def _record(context: RunContext, delivery: Delivery) -> None:
    context.save_artifact("delivery.json", delivery)
    lines = [f"- Doc: {delivery.doc_url} (verified: {'yes' if delivery.doc_verified else 'no'})"]
    if delivery.draft_id:
        lines.append(f"- Gmail draft: {delivery.draft_id} (not sent)")
    context.report.sections["Delivery"] = "\n".join(lines)


def _tool_failure(context: RunContext, error: ToolCallFailed, stage: str, status: RunStatus) -> RunStatus:
    line = TOOL_FAILURE.format(error=error, week=context.window.iso_week, stage=stage)
    context.report.warnings.append(line)
    context.report.sections["Delivery"] = line
    return status


def _no_gateway(context: RunContext) -> RunStatus:
    context.report.sections["Delivery"] = (
        "No MCP server configured: fill in config/mcp.yaml, then run pulse doctor."
    )
    return RunStatus.FAILED
