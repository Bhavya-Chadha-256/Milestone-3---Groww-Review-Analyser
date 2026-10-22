"""The allowlisted gateway to a Google Workspace MCP server (architecture.md §7, Task 5.1).

Our code never holds Google credentials: the MCP server runs its own OAuth flow. `config/mcp.yaml` maps
logical operations to the chosen server's tool names, and only allowlisted, non-destructive tools are
ever called. Logs carry tool names, field names, result IDs and latency, never field values.
"""

import json
import logging
import os
import re
import string
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import anyio
import yaml
from mcp import Client
from mcp.client.stdio import StdioServerParameters, get_default_environment
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from pydantic import BaseModel, ConfigDict, PositiveInt

LOG = logging.getLogger(__name__)
FORBIDDEN_TOOL_RE = re.compile(
    r"send|share|delete|trash|remove|permission|publish|forward|transfer|ownership|move|reply", re.IGNORECASE
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerSpec(_Strict):
    transport: Literal["stdio", "streamable_http"]
    command: list[str] | None = None
    url: str | None = None
    env: list[str] = []  # names of variables passed to a stdio server; values stay in .env
    auth_token_env: str | None = None  # name of the .env variable sent as an HTTP bearer token
    timeout_seconds: PositiveInt = 60  # per request, so a hung server can't hold the week's lock forever


class OperationSpec(_Strict):
    tool: str
    args: dict[str, str]
    optional_args: list[str] = []
    requires_id: bool = False
    id_regex: str | None = None
    url_regex: str | None = None
    not_found_regex: str | None = None  # an error matching it means "no such resource" (DEL-5)


class McpConfig(_Strict):
    server: ServerSpec
    operations: dict[str, OperationSpec]
    allowlist: list[str]


@dataclass(frozen=True)
class ToolResult:
    resource_id: str | None
    url: str | None
    text: str


class MissingTool(Exception):
    pass


class ForbiddenTool(Exception):
    pass


class ToolCallFailed(Exception):
    pass


class NotFound(ToolCallFailed):
    """The tool says the resource doesn't exist (its error matched `not_found_regex`)."""


class McpConfigError(Exception):
    """config/mcp.yaml is missing or can't be used."""


class NotAuthorised(Exception):
    """The MCP server reports that its Google account isn't authorised (e.g. an expired refresh token)."""


def load_mcp_config(path: Path) -> McpConfig:
    return McpConfig.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


class WorkspaceGateway:
    def __init__(self, client: Client, cfg: McpConfig) -> None:
        self.client = client
        self.cfg = cfg

    async def check_tools(self) -> None:
        """Fail fast, before any LLM spend, if a mapped tool is forbidden or missing (DEL-1, DEL-2, DEL-3)."""
        mapped = sorted({spec.tool for spec in self.cfg.operations.values()})
        forbidden = [tool for tool in mapped if FORBIDDEN_TOOL_RE.search(tool)]
        if forbidden:
            raise ForbiddenTool(f"config/mcp.yaml maps forbidden tools: {', '.join(forbidden)}")
        available = await self._tool_names()
        missing = [tool for tool in mapped if tool not in available]
        if missing:
            raise MissingTool(f"the MCP server has no tool named: {', '.join(missing)}")

    async def check_auth(self) -> None:
        """If the server maps `auth_status`, fail before any LLM spend unless Google access works (DEL-17)."""
        if not self.supports("auth_status"):
            return
        result = await self.call("auth_status")
        if not re.search(r'"authorized":\s*true', result.text):
            hint = _capture(r'"instructions":\s*"([^"]*)"', result.text) or "re-authorise the MCP server"
            raise NotAuthorised(f"the Google account is not authorised on the MCP server: {hint}")

    def supports(self, op: str) -> bool:
        """Whether this server offers the operation (an append-only server has no create, read or draft)."""
        return op in self.cfg.operations and op in self.cfg.allowlist

    async def call(self, op: str, **fields: str) -> ToolResult:
        spec = self.cfg.operations.get(op)
        if spec is None or op not in self.cfg.allowlist:
            raise ForbiddenTool(f"operation {op!r} is not in the allowlist")
        if FORBIDDEN_TOOL_RE.search(spec.tool):
            raise ForbiddenTool(f"tool {spec.tool!r} is forbidden")
        arguments = _arguments(spec, fields)
        started = time.monotonic()
        result = await self.client.call_tool(spec.tool, arguments)
        blocks = "\n".join(block.text for block in result.content if getattr(block, "text", None))
        if result.is_error:
            missing = spec.not_found_regex and re.search(spec.not_found_regex, blocks)
            raise (NotFound if missing else ToolCallFailed)(f"{spec.tool}: {blocks[:300]}")
        text = json.dumps(result.structured_content or {}) + "\n" + blocks
        resource_id, url = _capture(spec.id_regex, text), _capture(spec.url_regex, text)
        if spec.requires_id and resource_id is None:  # DEL-13
            raise ToolCallFailed(f"no id in result for {op}")
        elapsed = (time.monotonic() - started) * 1000
        LOG.info("mcp %s fields=%s id=%s %.0fms", spec.tool, sorted(fields), resource_id, elapsed)
        return ToolResult(resource_id, url, text)

    async def _tool_names(self) -> set[str]:
        names: set[str] = set()
        cursor = None
        while True:
            page = await self.client.list_tools(cursor=cursor)
            names |= {tool.name for tool in page.tools}
            cursor = page.next_cursor
            if not cursor:
                return names


@asynccontextmanager
async def open_gateway(cfg: McpConfig) -> AsyncIterator[WorkspaceGateway]:
    timeout = cfg.server.timeout_seconds
    if cfg.server.transport == "stdio":
        async with Client(_server_target(cfg.server), read_timeout_seconds=timeout) as client:
            yield WorkspaceGateway(client, cfg)
        return
    async with create_mcp_http_client(headers=_http_headers(cfg.server)) as http:
        transport = streamable_http_client(_server_target(cfg.server), http_client=http)
        async with Client(transport, read_timeout_seconds=timeout) as client:
            yield WorkspaceGateway(client, cfg)


def broken_gateway(reason: str) -> Callable[[], AbstractAsyncContextManager[WorkspaceGateway]]:
    """A factory for an unusable config/mcp.yaml: the pre-check and doctor report `reason`; no crash."""

    def connect() -> AbstractAsyncContextManager[WorkspaceGateway]:
        raise McpConfigError(reason)

    return connect


def gateway_problem(factory: Callable[[], AbstractAsyncContextManager[WorkspaceGateway]]) -> str | None:
    """Why the MCP server can't be used, or None: the pre-check before any LLM spend (DEL-1) and doctor."""

    async def check() -> None:
        async with factory() as gateway:
            await gateway.check_tools()
            await gateway.check_auth()

    try:
        _run(check)
    except (MissingTool, ForbiddenTool, McpConfigError, NotAuthorised) as error:
        return str(error)
    except Exception as error:  # an unreachable server can fail in many ways; report, don't crash
        return f"cannot reach the MCP server ({type(error).__name__}: {error})"
    return None


def run_with_gateway(
    factory: Callable[[], AbstractAsyncContextManager[WorkspaceGateway]],
    operation: Callable[[WorkspaceGateway], Awaitable[Any]],
) -> Any:
    """Run one async delivery operation from synchronous pipeline code. Any failure, including a server
    that won't start, crashes or times out, is a `ToolCallFailed`, so the stage reports the `--from` step."""

    async def run() -> Any:
        async with factory() as gateway:
            return await operation(gateway)

    try:
        return _run(run)
    except ToolCallFailed:
        raise
    except Exception as error:
        raise ToolCallFailed(f"{type(error).__name__}: {error}") from error


def _run(function: Callable[[], Awaitable[Any]]) -> Any:
    """`anyio.run`, re-raising the first real error instead of the client's task-group wrapper."""
    try:
        return anyio.run(function)
    except BaseExceptionGroup as group:
        raise _first(group) from group


def _first(group: BaseExceptionGroup) -> BaseException:
    found = group.exceptions[0]
    return _first(found) if isinstance(found, BaseExceptionGroup) else found


def _server_target(server: ServerSpec) -> StdioServerParameters | str:
    if server.transport == "streamable_http":
        if not server.url:
            raise ValueError("config/mcp.yaml: streamable_http needs server.url")
        return server.url
    if not server.command:
        raise ValueError("config/mcp.yaml: stdio needs server.command")
    passed = {name: os.environ[name] for name in server.env if os.environ.get(name)}  # "" would mask defaults
    return StdioServerParameters(
        command=server.command[0], args=server.command[1:], env=get_default_environment() | passed
    )


def _http_headers(server: ServerSpec) -> dict[str, str]:
    """The bearer header for an HTTP server; the token is read from .env and never logged or stored."""
    if not server.auth_token_env:
        return {}
    token = os.environ.get(server.auth_token_env)
    if not token:
        raise McpConfigError(f"{server.auth_token_env} is not set; add the MCP server's token to .env")
    return {"Authorization": f"Bearer {token}"}


def _arguments(spec: OperationSpec, fields: Mapping[str, str]) -> dict[str, Any]:
    """Fill each mapped argument; an optional one is dropped when its fields are missing or empty (DEL-12)."""
    arguments = {}
    for name, template in spec.args.items():
        needed = [field for _, field, _, _ in string.Formatter().parse(template) if field]
        if name in spec.optional_args and not all(fields.get(field) for field in needed):
            continue
        try:
            arguments[name] = template.format_map(fields)
        except KeyError as error:
            raise McpConfigError(f"config/mcp.yaml: {spec.tool}.{name} uses unknown field {error}") from error
    return arguments


def _capture(pattern: str | None, text: str) -> str | None:
    found = re.search(pattern, text) if pattern else None
    return found.group(1) if found else None
