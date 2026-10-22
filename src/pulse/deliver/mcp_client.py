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
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from mcp import Client
from mcp.client.stdio import StdioServerParameters, get_default_environment
from pydantic import BaseModel, ConfigDict

LOG = logging.getLogger(__name__)
FORBIDDEN_TOOL_RE = re.compile(r"send|share|delete|trash|remove|permission|publish|forward", re.IGNORECASE)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerSpec(_Strict):
    transport: Literal["stdio", "streamable_http"]
    command: list[str] | None = None
    url: str | None = None
    env: list[str] = []  # names of variables passed to a stdio server; values stay in .env


class OperationSpec(_Strict):
    tool: str
    args: dict[str, str]
    optional_args: list[str] = []
    requires_id: bool = False
    id_regex: str | None = None
    url_regex: str | None = None


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

    async def call(self, op: str, **fields: str) -> ToolResult:
        spec = self.cfg.operations.get(op)
        if spec is None or op not in self.cfg.allowlist:
            raise ForbiddenTool(f"operation {op!r} is not in the allowlist")
        if FORBIDDEN_TOOL_RE.search(spec.tool):
            raise ForbiddenTool(f"tool {spec.tool!r} is forbidden")
        started = time.monotonic()
        result = await self.client.call_tool(spec.tool, _arguments(spec, fields))
        blocks = "\n".join(block.text for block in result.content if getattr(block, "text", None))
        if result.is_error:
            raise ToolCallFailed(f"{spec.tool}: {blocks[:300]}")
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
    async with Client(_server_target(cfg.server)) as client:
        yield WorkspaceGateway(client, cfg)


def _server_target(server: ServerSpec) -> StdioServerParameters | str:
    if server.transport == "streamable_http":
        if not server.url:
            raise ValueError("config/mcp.yaml: streamable_http needs server.url")
        return server.url
    if not server.command:
        raise ValueError("config/mcp.yaml: stdio needs server.command")
    passed = {name: os.environ[name] for name in server.env if name in os.environ}
    return StdioServerParameters(
        command=server.command[0], args=server.command[1:], env=get_default_environment() | passed
    )


def _arguments(spec: OperationSpec, fields: Mapping[str, str]) -> dict[str, Any]:
    """Fill each mapped argument; an optional one is left out when its fields are missing or empty (DEL-12)."""
    arguments = {}
    for name, template in spec.args.items():
        needed = [field for _, field, _, _ in string.Formatter().parse(template) if field]
        if name in spec.optional_args and not all(fields.get(field) for field in needed):
            continue
        arguments[name] = template.format_map(fields)
    return arguments


def _capture(pattern: str | None, text: str) -> str | None:
    found = re.search(pattern, text) if pattern else None
    return found.group(1) if found else None
