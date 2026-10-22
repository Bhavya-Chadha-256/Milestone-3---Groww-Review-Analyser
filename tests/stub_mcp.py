"""An in-process stand-in for a Google Workspace MCP server (Task 5.1). Tests reset `STATE` per test."""

from dataclasses import dataclass, field
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError


@dataclass
class StubState:
    docs: dict[str, str] = field(default_factory=dict)
    drafts: dict[str, dict[str, str]] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    fail_next: set[str] = field(default_factory=set)
    stale_reads: bool = False  # get_document returns "" instead of the content
    omit_ids: bool = False  # create tools return no IDs (DEL-13)
    created: int = 0  # IDs are never reused, even after `docs` is cleared

    def reset(self) -> None:
        self.__init__()


STATE = StubState()
server = MCPServer("stub-workspace")


def _record(tool: str, **args: Any) -> None:
    STATE.calls.append((tool, args))
    if tool in STATE.fail_next:
        STATE.fail_next.discard(tool)
        raise ToolError(f"{tool} failed: invalid_grant (token expired)")


@server.tool()
def create_document(title: str, content: str, folder_id: str) -> dict[str, str]:
    _record("create_document", title=title, content=content, folder_id=folder_id)
    STATE.created += 1
    doc_id = f"doc{STATE.created}"
    STATE.docs[doc_id] = content
    if STATE.omit_ids:
        return {"status": "created"}
    return {"documentId": doc_id, "url": f"https://docs.google.com/document/d/{doc_id}/edit"}


@server.tool()
def update_document(document_id: str, content: str) -> dict[str, str]:
    _record("update_document", document_id=document_id, content=content)
    if document_id not in STATE.docs:
        raise ToolError(f"document {document_id} not found")
    STATE.docs[document_id] = content
    return {"documentId": document_id, "url": f"https://docs.google.com/document/d/{document_id}/edit"}


@server.tool()
def get_document(document_id: str) -> dict[str, str]:
    _record("get_document", document_id=document_id)
    if document_id not in STATE.docs:
        raise ToolError(f"document {document_id} not found")
    return {"documentId": document_id, "content": "" if STATE.stale_reads else STATE.docs[document_id]}


@server.tool()
def create_gmail_draft(recipient: str, subject: str, body: str, html_body: str = "") -> dict[str, str]:
    _record(
        "create_gmail_draft",
        recipient=recipient,
        subject=subject,
        body=body,
        **({"html_body": html_body} if html_body else {}),
    )
    draft_id = f"draft{len(STATE.drafts) + 1}"
    STATE.drafts[draft_id] = {
        "recipient": recipient,
        "subject": subject,
        "body": body,
        "html_body": html_body,
    }
    return {"status": "created"} if STATE.omit_ids else {"draftId": draft_id}


@server.tool()
def send_gmail_message(recipient: str, subject: str, body: str) -> dict[str, str]:
    """Exists so tests can prove that sending is refused before any call (DEL-3)."""
    _record("send_gmail_message", recipient=recipient, subject=subject, body=body)
    return {"status": "sent"}
