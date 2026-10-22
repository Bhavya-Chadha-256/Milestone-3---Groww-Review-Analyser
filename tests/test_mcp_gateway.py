from pathlib import Path

import pytest
from mcp import Client
from stub_mcp import STATE

from pulse.deliver.mcp_client import (
    FORBIDDEN_TOOL_RE,
    ForbiddenTool,
    McpConfig,
    MissingTool,
    OperationSpec,
    ToolCallFailed,
    WorkspaceGateway,
    load_mcp_config,
)

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config" / "mcp.yaml"


def with_operation(config: McpConfig, name: str, spec: OperationSpec, *, allow: bool = True) -> McpConfig:
    allowlist = [*config.allowlist, name] if allow else config.allowlist
    return config.model_copy(update={"operations": config.operations | {name: spec}, "allowlist": allowlist})


@pytest.mark.anyio
async def test_check_tools_passes_with_stub(gateway: WorkspaceGateway) -> None:
    await gateway.check_tools()


@pytest.mark.anyio
async def test_missing_tool_is_reported(client: Client, stub_config: McpConfig) -> None:  # DEL-2
    config = with_operation(stub_config, "export_pdf", OperationSpec(tool="export_doc_pdf", args={}))
    with pytest.raises(MissingTool, match="export_doc_pdf"):
        await WorkspaceGateway(client, config).check_tools()


@pytest.mark.anyio
async def test_create_doc_extracts_id_and_url(gateway: WorkspaceGateway) -> None:
    result = await gateway.call("create_doc", title="T", body="B", folder_id="F")
    assert result.resource_id == "doc1" and result.url == "https://docs.google.com/document/d/doc1/edit"
    assert STATE.calls == [("create_document", {"title": "T", "content": "B", "folder_id": "F"})]


@pytest.mark.anyio
async def test_forbidden_tool_refused_before_any_call(
    client: Client, stub_config: McpConfig
) -> None:  # DEL-3
    send = OperationSpec(
        tool="send_gmail_message", args={"recipient": "{recipient}", "subject": "{subject}", "body": "{text}"}
    )
    gateway = WorkspaceGateway(client, with_operation(stub_config, "send", send))
    with pytest.raises(ForbiddenTool, match="send_gmail_message"):
        await gateway.check_tools()
    with pytest.raises(ForbiddenTool):
        await gateway.call("send", recipient="a@b.c", subject="S", text="T")
    assert STATE.calls == []
    assert all(FORBIDDEN_TOOL_RE.search(name) for name in ("delete_doc", "share_drive_file", "trash_file"))


@pytest.mark.anyio
async def test_op_not_in_allowlist_refused(gateway: WorkspaceGateway) -> None:
    with pytest.raises(ForbiddenTool, match="not in the allowlist"):
        await gateway.call("delete_doc", doc_id="doc1")
    assert STATE.calls == []


@pytest.mark.anyio
async def test_tool_error_raises_tool_call_failed(gateway: WorkspaceGateway) -> None:  # DEL-4
    STATE.fail_next = {"create_document"}
    with pytest.raises(ToolCallFailed, match="invalid_grant"):
        await gateway.call("create_doc", title="T", body="B", folder_id="F")


@pytest.mark.anyio
async def test_create_without_extractable_id_fails(gateway: WorkspaceGateway) -> None:  # DEL-13
    STATE.omit_ids = True
    with pytest.raises(ToolCallFailed, match="no id"):
        await gateway.call("create_doc", title="T", body="B", folder_id="F")


@pytest.mark.anyio
async def test_unmapped_optional_field_is_not_sent(client: Client, stub_config: McpConfig) -> None:  # DEL-12
    gateway = WorkspaceGateway(client, stub_config)
    await gateway.call("create_draft", recipient="a@b.c", subject="S", text="T", html="")
    plain = stub_config.operations["create_draft"].model_copy(
        update={"args": {"recipient": "{recipient}", "subject": "{subject}", "body": "{text}"}}
    )
    unmapped = WorkspaceGateway(client, with_operation(stub_config, "create_draft", plain, allow=False))
    await unmapped.call("create_draft", recipient="a@b.c", subject="S", text="T", html="<p>T</p>")
    assert [args for _, args in STATE.calls] == [{"recipient": "a@b.c", "subject": "S", "body": "T"}] * 2


def test_repo_config_loads_with_placeholder_tools() -> None:
    config = load_mcp_config(REPO_CONFIG)
    assert set(config.allowlist) == {"create_doc", "update_doc", "read_doc", "create_draft"}
    assert config.operations["create_doc"].requires_id and "html" in str(
        config.operations["create_draft"].args
    )
