import pytest
from stub_mcp import STATE

from pulse.deliver.docs import publish_doc
from pulse.deliver.gmail import create_draft
from pulse.deliver.mcp_client import ToolCallFailed, WorkspaceGateway
from pulse.store import Store

WEEK = "2026-W42"


async def publish(
    gateway: WorkspaceGateway, store: Store, body: str, must_contain: list[str] | None = None
) -> tuple:
    return await publish_doc(
        gateway,
        store,
        iso_week=WEEK,
        title="Groww Review Pulse — 2026-W42",
        body=body,
        folder_id="F",
        must_contain=must_contain if must_contain is not None else [body],
    )


def tools_called() -> list[str]:
    return [tool for tool, _ in STATE.calls]


@pytest.mark.anyio
async def test_first_publish_creates_then_rerun_updates_same_doc(
    gateway: WorkspaceGateway, tmp_store: Store
) -> None:
    first, warnings = await publish(gateway, tmp_store, "Withdrawals — “stuck for days”")
    assert first.doc_id == "doc1" and first.doc_verified and warnings == []
    assert first.doc_url == "https://docs.google.com/document/d/doc1/edit"
    second, _ = await publish(gateway, tmp_store, "Charges — new week")
    assert second.doc_id == "doc1" and len(STATE.docs) == 1 and STATE.docs["doc1"] == "Charges — new week"
    assert tools_called() == ["create_document", "get_document", "update_document", "get_document"]
    assert tmp_store.get_delivery(WEEK).doc_id == "doc1"


@pytest.mark.anyio
async def test_read_back_mismatch_retries_once_then_unverified(
    gateway: WorkspaceGateway, tmp_store: Store
) -> None:  # DEL-6
    STATE.stale_reads = True
    delivery, _ = await publish(gateway, tmp_store, "Body text")
    assert delivery.doc_verified is False and tmp_store.get_delivery(WEEK).doc_verified is False
    assert tools_called() == ["create_document", "get_document", "update_document", "get_document"]


@pytest.mark.anyio
async def test_update_on_missing_doc_recreates(gateway: WorkspaceGateway, tmp_store: Store) -> None:  # DEL-5
    first, _ = await publish(gateway, tmp_store, "A")
    STATE.docs.clear()
    second, warnings = await publish(gateway, tmp_store, "B")
    assert second.doc_id != first.doc_id and "doc_recreated" in warnings
    assert tmp_store.get_delivery(WEEK).doc_id == second.doc_id


@pytest.mark.anyio
async def test_update_failure_on_existing_doc_is_raised(
    gateway: WorkspaceGateway, tmp_store: Store
) -> None:  # DEL-4
    await publish(gateway, tmp_store, "A")
    STATE.fail_next = {"update_document"}
    with pytest.raises(ToolCallFailed, match="invalid_grant"):
        await publish(gateway, tmp_store, "B")
    assert len(STATE.docs) == 1


@pytest.mark.anyio
async def test_ledger_is_saved_right_after_create(
    gateway: WorkspaceGateway, tmp_store: Store
) -> None:  # DEL-9
    STATE.fail_next = {"get_document"}
    with pytest.raises(ToolCallFailed):
        await publish(gateway, tmp_store, "A")
    assert tmp_store.get_delivery(WEEK).doc_id == "doc1"


async def draft(gateway: WorkspaceGateway, store: Store, *, force: bool = False) -> object:
    return await create_draft(
        gateway,
        store,
        iso_week=WEEK,
        recipient="me@example.com",
        subject="Groww Review Pulse — 2026-W42",
        text="Plain body",
        html="<p>Plain body</p>",
        force=force,
    )


@pytest.mark.anyio
async def test_draft_is_idempotent_unless_forced(
    gateway: WorkspaceGateway, tmp_store: Store
) -> None:  # DEL-7
    first = await draft(gateway, tmp_store)
    again = await draft(gateway, tmp_store)
    assert first.draft_id == again.draft_id == "draft1" and len(STATE.drafts) == 1
    forced = await draft(gateway, tmp_store, force=True)
    assert forced.draft_id == "draft2" and tmp_store.get_delivery(WEEK).draft_id == "draft2"
    assert STATE.drafts["draft1"]["recipient"] == "me@example.com" and forced.delivered_at is not None
