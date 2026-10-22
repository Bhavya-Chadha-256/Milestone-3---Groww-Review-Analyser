"""Publish the week's pulse to one Google Doc per ISO week, idempotently (architecture.md §7, Task 5.2).

The delivery ledger (`deliveries` table) is saved right after every create call, so a re-run updates the
same Doc instead of making a second one (DEL-9).
"""

import json
from collections.abc import Sequence
from typing import Any

from pulse.deliver.mcp_client import ToolCallFailed, ToolResult, WorkspaceGateway
from pulse.llm.quotes import normalize_quote_text
from pulse.models import Delivery
from pulse.store import Store


async def publish_doc(  # craftsman-ignore: WARN-PY001, PY003 (plan interface; hint on closing line)
    gw: WorkspaceGateway,
    store: Store,
    *,
    iso_week: str,
    title: str,
    body: str,
    folder_id: str,
    must_contain: Sequence[str],
) -> tuple[Delivery, list[str]]:
    """The week's Doc, created or updated, and read back; returns the ledger entry and any warnings."""
    delivery = store.get_delivery(iso_week) or Delivery(iso_week=iso_week)
    delivery, warnings = await _write(gw, store, delivery, title=title, body=body, folder_id=folder_id)
    verified = await _contains(gw, delivery.doc_id, must_contain)
    if not verified:  # DEL-6: one more write and read
        await gw.call("update_doc", doc_id=delivery.doc_id, body=body)
        verified = await _contains(gw, delivery.doc_id, must_contain)
    delivery = delivery.model_copy(update={"doc_verified": verified})
    store.save_delivery(delivery)
    return delivery, warnings


async def _write(  # craftsman-ignore: WARN-PY001, PY003 (hint on closing line)
    gw: WorkspaceGateway, store: Store, delivery: Delivery, *, title: str, body: str, folder_id: str
) -> tuple[Delivery, list[str]]:
    """Update the week's Doc, or create it when there is none or it was deleted by hand (DEL-5)."""
    if delivery.doc_id is None:
        return await _create(gw, store, delivery, title=title, body=body, folder_id=folder_id), []
    try:
        await gw.call("update_doc", doc_id=delivery.doc_id, body=body)
    except ToolCallFailed:
        if await _exists(gw, delivery.doc_id):
            raise  # the Doc is there, so the failure is real (e.g. expired auth, DEL-4)
        recreated = await _create(gw, store, delivery, title=title, body=body, folder_id=folder_id)
        return recreated, ["doc_recreated"]
    return delivery, []


async def _create(  # craftsman-ignore: WARN-PY001, PY003 (hint on closing line)
    gw: WorkspaceGateway, store: Store, delivery: Delivery, *, title: str, body: str, folder_id: str
) -> Delivery:
    result = await gw.call("create_doc", title=title, body=body, folder_id=folder_id)
    created = delivery.model_copy(
        update={"doc_id": result.resource_id, "doc_url": result.url, "doc_verified": False}
    )
    store.save_delivery(created)  # immediately, before anything else can fail (DEL-9)
    return created


async def _exists(gw: WorkspaceGateway, doc_id: str) -> bool:
    try:
        await gw.call("read_doc", doc_id=doc_id)
    except ToolCallFailed:
        return False
    return True


async def _contains(gw: WorkspaceGateway, doc_id: str, must_contain: Sequence[str]) -> bool:
    text = _readable(await gw.call("read_doc", doc_id=doc_id))
    return all(normalize_quote_text(item) in text for item in must_contain)


def _readable(result: ToolResult) -> str:
    """The read-back as plain text: the raw result plus every string inside JSON parts (escapes undone)."""
    parts = [result.text]
    for chunk in result.text.split("\n", 1):
        try:
            parts += _strings(json.loads(chunk))
        except ValueError:
            continue
    return normalize_quote_text(" ".join(parts))


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []
