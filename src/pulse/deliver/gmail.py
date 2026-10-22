"""Create the week's Gmail draft once; nothing is ever sent (architecture.md §7, Task 5.2)."""

from datetime import UTC, datetime

from pulse.deliver.mcp_client import WorkspaceGateway
from pulse.models import Delivery
from pulse.store import Store


async def create_draft(  # craftsman-ignore: WARN-PY001, PY003 (plan interface; hint on closing line)
    gw: WorkspaceGateway,
    store: Store,
    *,
    iso_week: str,
    recipient: str,
    subject: str,
    text: str,
    html: str,
    force: bool = False,
) -> Delivery:
    """The week's draft; an existing one is returned with no tool call unless `force` (DEL-7)."""
    delivery = store.get_delivery(iso_week) or Delivery(iso_week=iso_week)
    if delivery.draft_id and not force:
        return delivery
    result = await gw.call("create_draft", recipient=recipient, subject=subject, text=text, html=html)
    delivery = delivery.model_copy(update={"draft_id": result.resource_id, "delivered_at": datetime.now(UTC)})
    store.save_delivery(delivery)
    return delivery
