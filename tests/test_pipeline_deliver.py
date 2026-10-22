import json
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime

from conftest import GatewayFactory, stub_factory
from fakes import FakeLLM
from stub_mcp import STATE
from test_pipeline_pulse import TODAY, calls, classify, drafter, pick_quote, seed

from pulse.config import Settings
from pulse.deliver.mcp_client import McpConfig, WorkspaceGateway
from pulse.models import RunStatus
from pulse.pipeline import RunOptions, RunOutcome, run_pipeline
from pulse.store import Store

WEEK = "2026-W42"


def pulse_llm(compose: object = None) -> FakeLLM:
    return FakeLLM({"classify": classify, "quotes": pick_quote, "compose": compose or drafter()})


def deliver(  # craftsman-ignore: WARN-PY001 (test helper)
    settings: Settings, store: Store, llm: FakeLLM, factory: GatewayFactory | None, **opts: object
) -> RunOutcome:
    fields: dict[str, object] = {"today": TODAY, "started_at": datetime(2026, 10, 19, 5, tzinfo=UTC)}
    options = RunOptions(**(fields | opts))
    return run_pipeline(settings, options, store=store, sources=[], llm=llm, gateway_factory=factory)


def test_full_run_delivers(tmp_settings: Settings, tmp_store: Store, gateway_factory: GatewayFactory) -> None:
    seed(tmp_store)
    out = deliver(tmp_settings, tmp_store, pulse_llm(), gateway_factory)
    assert out.status == RunStatus.DELIVERED and len(STATE.docs) == 1 and len(STATE.drafts) == 1
    draft = STATE.drafts["draft1"]
    assert "Full pulse: https://docs.google.com/document/d/doc1/edit" in draft["body"]
    assert 'href="https://docs.google.com/document/d/doc1/edit"' in draft["html_body"]
    assert (
        draft["subject"] == "Groww Review Pulse — 2026-W42"
        and draft["recipient"] == tmp_settings.delivery.recipient
    )
    assert STATE.calls[0][1]["title"] == "Groww Review Pulse — 2026-W42 (12–18 Oct)"
    for name in ("quotes.json", "pulse_draft.json", "pulse.md", "validation.json", "delivery.json"):
        assert (out.run_dir / name).is_file()
    assert json.loads((out.run_dir / "delivery.json").read_text())["doc_verified"] is True


def test_dry_run_makes_no_gateway_calls(
    tmp_settings: Settings, tmp_store: Store, gateway_factory: GatewayFactory
) -> None:
    seed(tmp_store)
    out = deliver(tmp_settings, tmp_store, pulse_llm(), gateway_factory, until_stage="validate")
    assert out.status == RunStatus.VALIDATED and STATE.calls == []
    assert not (out.run_dir / "delivery.json").exists()


def test_validation_failure_delivers_nothing(
    tmp_settings: Settings, tmp_store: Store, gateway_factory: GatewayFactory
) -> None:  # OPS-4
    seed(tmp_store)
    wordy = drafter(lambda theme: " ".join(["slow"] * 19))
    out = deliver(tmp_settings, tmp_store, pulse_llm(wordy), gateway_factory)
    assert out.status == RunStatus.VALIDATION_FAILED and STATE.calls == []


def test_publish_refuses_a_pulse_that_failed_validation(
    tmp_settings: Settings, tmp_store: Store, gateway_factory: GatewayFactory
) -> None:
    seed(tmp_store)
    wordy = drafter(lambda theme: " ".join(["slow"] * 19))
    deliver(tmp_settings, tmp_store, pulse_llm(wordy), gateway_factory)
    resumed = deliver(tmp_settings, tmp_store, pulse_llm(), gateway_factory, from_stage="publish")
    assert resumed.status == RunStatus.VALIDATION_FAILED and STATE.calls == []


def test_missing_mcp_tool_fails_before_llm_spend(
    tmp_settings: Settings, tmp_store: Store, stub_config: McpConfig
) -> None:  # DEL-1
    seed(tmp_store)
    renamed = stub_config.operations["create_doc"].model_copy(update={"tool": "create_doc_v2"})
    broken = stub_config.model_copy(update={"operations": stub_config.operations | {"create_doc": renamed}})
    llm = pulse_llm()
    out = deliver(tmp_settings, tmp_store, llm, stub_factory(broken))
    assert out.status == RunStatus.FAILED and llm.calls == [] and STATE.calls == []
    assert "create_doc_v2" in (out.run_dir / "report.md").read_text()


def test_resume_from_draft_after_draft_failure(
    tmp_settings: Settings, tmp_store: Store, gateway_factory: GatewayFactory
) -> None:  # DEL-8
    seed(tmp_store)
    STATE.fail_next = {"create_gmail_draft"}
    first = deliver(tmp_settings, tmp_store, pulse_llm(), gateway_factory)
    assert first.status == RunStatus.PUBLISHED_DOC_ONLY and len(STATE.docs) == 1 and STATE.drafts == {}
    assert "--from draft" in (first.run_dir / "report.md").read_text()
    llm = pulse_llm()
    resumed = deliver(tmp_settings, tmp_store, llm, gateway_factory, from_stage="draft")
    assert resumed.status == RunStatus.DELIVERED and len(STATE.docs) == 1 and len(STATE.drafts) == 1
    assert calls(llm, "compose") == 0


def test_tool_failure_message_reaches_report(
    tmp_settings: Settings, tmp_store: Store, gateway_factory: GatewayFactory
) -> None:  # DEL-4
    seed(tmp_store)
    STATE.fail_next = {"create_document"}
    out = deliver(tmp_settings, tmp_store, pulse_llm(), gateway_factory)
    assert out.status == RunStatus.FAILED
    report = (out.run_dir / "report.md").read_text()
    assert "re-authorise" in report and "pulse run --week 2026-W42 --from publish" in report


def failing_from(factory: GatewayFactory, call_number: int) -> GatewayFactory:
    """`factory`, except that connection `call_number` onwards (1-based) fails as an unreachable server."""
    opened = []

    def connect() -> AbstractAsyncContextManager[WorkspaceGateway]:
        opened.append(1)
        if len(opened) >= call_number:
            raise OSError("server process exited")
        return factory()

    return connect


def test_draft_refuses_a_pulse_that_failed_validation(
    tmp_settings: Settings, tmp_store: Store, gateway_factory: GatewayFactory
) -> None:  # OPS-4
    seed(tmp_store)
    STATE.fail_next = {"create_gmail_draft"}
    deliver(tmp_settings, tmp_store, pulse_llm(), gateway_factory)
    wordy = drafter(lambda theme: " ".join(["slow"] * 19))
    later = datetime(2026, 10, 19, 6, tzinfo=UTC)
    rerun = deliver(tmp_settings, tmp_store, pulse_llm(wordy), gateway_factory, started_at=later)
    assert rerun.status == RunStatus.VALIDATION_FAILED
    resumed = deliver(tmp_settings, tmp_store, pulse_llm(), gateway_factory, from_stage="draft", force=True)
    assert resumed.status == RunStatus.VALIDATION_FAILED and STATE.drafts == {}


def test_gateway_crash_during_draft_keeps_doc_only(
    tmp_settings: Settings, tmp_store: Store, gateway_factory: GatewayFactory
) -> None:  # DEL-8
    seed(tmp_store)
    out = deliver(tmp_settings, tmp_store, pulse_llm(), failing_from(gateway_factory, 3))
    assert out.status == RunStatus.PUBLISHED_DOC_ONLY and len(STATE.docs) == 1
    report = (out.run_dir / "report.md").read_text()
    assert "OSError" in report and "--from draft" in report


def test_unreachable_server_fails_before_llm_spend(
    tmp_settings: Settings, tmp_store: Store, gateway_factory: GatewayFactory
) -> None:  # DEL-1
    seed(tmp_store)
    llm = pulse_llm()
    out = deliver(tmp_settings, tmp_store, llm, failing_from(gateway_factory, 1))
    assert out.status == RunStatus.FAILED and llm.calls == []
    assert "cannot reach the MCP server" in (out.run_dir / "report.md").read_text()


def test_no_mcp_config_fails_before_llm_spend(tmp_settings: Settings, tmp_store: Store) -> None:  # DEL-1
    seed(tmp_store)
    llm = pulse_llm()
    out = deliver(tmp_settings, tmp_store, llm, None)
    assert out.status == RunStatus.FAILED and llm.calls == []
    assert "config/mcp.yaml" in (out.run_dir / "report.md").read_text()


def test_unverified_read_back_still_drafts(
    tmp_settings: Settings, tmp_store: Store, gateway_factory: GatewayFactory
) -> None:  # DEL-6
    seed(tmp_store)
    STATE.stale_reads = True
    out = deliver(tmp_settings, tmp_store, pulse_llm(), gateway_factory)
    assert out.status == RunStatus.DELIVERY_UNVERIFIED and len(STATE.drafts) == 1


def test_rerun_same_week_updates_doc_and_keeps_draft(
    tmp_settings: Settings, tmp_store: Store, gateway_factory: GatewayFactory
) -> None:  # DEL-7, E-DEL-2
    seed(tmp_store)
    deliver(tmp_settings, tmp_store, pulse_llm(), gateway_factory)
    later = datetime(2026, 10, 19, 6, tzinfo=UTC)
    rerun = deliver(tmp_settings, tmp_store, pulse_llm(), gateway_factory, started_at=later)
    assert rerun.status == RunStatus.DELIVERED and len(STATE.docs) == 1 and len(STATE.drafts) == 1
    assert [tool for tool, _ in STATE.calls].count("update_document") == 1


def test_precheck_warning_is_cleared_by_a_later_resume(
    tmp_settings: Settings, tmp_store: Store, gateway_factory: GatewayFactory
) -> None:  # OPS-11
    seed(tmp_store)
    deliver(tmp_settings, tmp_store, pulse_llm(), gateway_factory, until_stage="validate")
    down = deliver(
        tmp_settings, tmp_store, pulse_llm(), failing_from(gateway_factory, 1), from_stage="publish"
    )
    assert "mcp_precheck_failed" in (down.run_dir / "report.md").read_text()
    resumed = deliver(tmp_settings, tmp_store, pulse_llm(), gateway_factory, from_stage="publish")
    assert resumed.status == RunStatus.DELIVERED
    assert "mcp_precheck_failed" not in (resumed.run_dir / "report.md").read_text()
