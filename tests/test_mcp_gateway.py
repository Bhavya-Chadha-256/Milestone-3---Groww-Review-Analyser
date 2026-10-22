from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from conftest import stub_factory
from mcp import Client
from stub_mcp import STATE

from pulse.deliver import mcp_client
from pulse.deliver.mcp_client import (
    FORBIDDEN_TOOL_RE,
    ForbiddenTool,
    McpConfig,
    McpConfigError,
    MissingTool,
    NotFound,
    OperationSpec,
    ServerSpec,
    ToolCallFailed,
    WorkspaceGateway,
    _http_headers,
    _server_target,
    broken_gateway,
    gateway_problem,
    load_mcp_config,
    open_gateway,
    run_with_gateway,
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


def test_repo_config_maps_the_hosted_append_server() -> None:
    config = load_mcp_config(REPO_CONFIG)
    assert config.server.transport == "streamable_http" and config.server.auth_token_env == "MCP_AUTH_TOKEN"
    assert set(config.allowlist) == {"append_doc", "auth_status"} == set(config.operations)
    assert config.operations["append_doc"].tool == "gdocs_append_text"
    assert config.operations["append_doc"].requires_id
    assert not any(FORBIDDEN_TOOL_RE.search(spec.tool) for spec in config.operations.values())


@pytest.mark.anyio
async def test_not_found_error_is_distinguished(gateway: WorkspaceGateway) -> None:  # DEL-5
    with pytest.raises(NotFound, match="not found"):
        await gateway.call("read_doc", doc_id="missing")
    STATE.docs["doc9"] = "body"
    STATE.fail_next = {"get_document"}
    with pytest.raises(ToolCallFailed) as raised:
        await gateway.call("read_doc", doc_id="doc9")
    assert not isinstance(raised.value, NotFound)


@pytest.mark.anyio
async def test_unknown_template_field_is_a_config_error(client: Client, stub_config: McpConfig) -> None:
    typo = OperationSpec(tool="get_document", args={"document_id": "{document}"})
    gateway = WorkspaceGateway(client, with_operation(stub_config, "read_doc", typo, allow=False))
    with pytest.raises(McpConfigError, match="document"):
        await gateway.call("read_doc", doc_id="doc1")
    assert STATE.calls == []


def test_forbidden_regex_covers_ownership_and_replies() -> None:
    names = ("transfer_drive_ownership", "move_file", "reply_to_message")
    assert all(FORBIDDEN_TOOL_RE.search(name) for name in names)
    assert not any(
        FORBIDDEN_TOOL_RE.search(name) for name in ("create_doc", "get_doc_content", "draft_gmail")
    )


def test_server_passes_only_named_non_empty_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk-secret")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "")
    spec = ServerSpec(
        transport="stdio",
        command=["uvx", "workspace-mcp"],
        env=["GOOGLE_OAUTH_CLIENT_ID", "USER_GOOGLE_EMAIL"],
    )
    env = _server_target(spec).env
    assert env["GOOGLE_OAUTH_CLIENT_ID"] == "client-id"
    assert "GROQ_API_KEY" not in env and "USER_GOOGLE_EMAIL" not in env


@pytest.mark.anyio
async def test_client_gets_the_configured_timeout(
    stub_config: McpConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    @asynccontextmanager
    async def fake_client(target: object, **kwargs: object) -> AsyncIterator[object]:
        seen.update(kwargs)
        yield object()

    monkeypatch.setattr(mcp_client, "Client", fake_client)
    config = stub_config.model_copy(
        update={"server": ServerSpec(transport="streamable_http", url="http://x")}
    )
    async with open_gateway(config):
        pass
    assert seen == {"read_timeout_seconds": 60}


def test_connection_errors_become_tool_call_failed() -> None:  # DEL-8
    @asynccontextmanager
    async def unreachable() -> AsyncIterator[WorkspaceGateway]:
        raise OSError("uvx: command not found")
        yield  # pragma: no cover

    async def operation(gateway: WorkspaceGateway) -> None:
        return None

    with pytest.raises(ToolCallFailed, match="OSError: uvx: command not found"):
        run_with_gateway(unreachable, operation)


def test_broken_config_is_reported_not_raised() -> None:  # DEL-1
    problem = gateway_problem(broken_gateway("config/mcp.yaml: operations.create_doc.tool: missing"))
    assert problem == "config/mcp.yaml: operations.create_doc.tool: missing"


def test_bearer_header_comes_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", "tok-123")
    spec = ServerSpec(transport="streamable_http", url="https://x/mcp", auth_token_env="MCP_AUTH_TOKEN")
    assert _http_headers(spec) == {"Authorization": "Bearer tok-123"}
    monkeypatch.setenv("MCP_AUTH_TOKEN", "")
    with pytest.raises(McpConfigError, match="MCP_AUTH_TOKEN is not set"):
        _http_headers(spec)
    assert _http_headers(ServerSpec(transport="streamable_http", url="https://x/mcp")) == {}


def test_unauthorised_google_account_is_reported(append_config: McpConfig) -> None:  # DEL-17
    STATE.authorized = False
    problem = gateway_problem(stub_factory(append_config))
    assert problem is not None and "not authorised" in problem and "npx gmail-gdocs-mcp auth" in problem
    STATE.authorized = True
    assert gateway_problem(stub_factory(append_config)) is None
