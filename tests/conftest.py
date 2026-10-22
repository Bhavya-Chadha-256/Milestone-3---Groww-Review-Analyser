"""Shared pytest fixtures for every task (implementation.md Task 0.2)."""

import shutil
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

import pytest
import stub_mcp
from mcp import Client

from pulse.config import Settings, load_settings
from pulse.deliver.mcp_client import McpConfig, WorkspaceGateway, load_mcp_config
from pulse.store import Store

REPO_ROOT = Path(__file__).resolve().parents[1]
STUB_CONFIG = REPO_ROOT / "tests" / "fixtures" / "mcp_stub.yaml"
APPEND_CONFIG = REPO_ROOT / "tests" / "fixtures" / "mcp_append_stub.yaml"


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def fixtures_dir(repo_root: Path) -> Path:
    return repo_root / "tests" / "fixtures"


@pytest.fixture
def settings(repo_root: Path) -> Settings:
    return load_settings(repo_root / "config" / "pulse.yaml")


@pytest.fixture
def tmp_settings(settings: Settings, repo_root: Path, tmp_path: Path) -> Settings:
    """Repo settings with every path redirected into tmp_path, so tests never touch data/ or runs/."""
    mappings = tmp_path / "mappings"
    repo_mappings = repo_root / "config" / "mappings"
    if repo_mappings.is_dir():
        shutil.copytree(repo_mappings, mappings)
    else:
        mappings.mkdir()
    paths = settings.paths.model_copy(
        update={
            "db": tmp_path / "data" / "pulse.db",
            "runs": tmp_path / "runs",
            "raw": tmp_path / "data" / "raw",
            "mappings": mappings,
        }
    )
    return settings.model_copy(update={"paths": paths}, deep=True)


@pytest.fixture
def tmp_store(tmp_settings: Settings) -> Store:
    return Store(Path(tmp_settings.paths.db))


# MCP stub gateway (Phase 5): an in-process stand-in for the Workspace MCP server.


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def stub_config() -> McpConfig:
    stub_mcp.STATE.reset()
    return load_mcp_config(STUB_CONFIG)


@pytest.fixture
async def client(stub_config: McpConfig) -> AsyncIterator[Client]:
    async with Client(stub_mcp.server) as connected:
        yield connected


@pytest.fixture
def gateway(client: Client, stub_config: McpConfig) -> WorkspaceGateway:
    return WorkspaceGateway(client, stub_config)


GatewayFactory = Callable[[], AbstractAsyncContextManager[WorkspaceGateway]]


def stub_factory(config: McpConfig) -> GatewayFactory:
    """What `build_dependencies` provides for a real server, but connected to the in-process stub."""

    @asynccontextmanager
    async def connect() -> AsyncIterator[WorkspaceGateway]:
        async with Client(stub_mcp.server) as connected:
            yield WorkspaceGateway(connected, config)

    return connect


@pytest.fixture
def gateway_factory(stub_config: McpConfig) -> GatewayFactory:
    return stub_factory(stub_config)


@pytest.fixture
def append_config() -> McpConfig:
    """An append-only server (like gmail-gdocs-mcp): one running Doc, `doc0`, already exists."""
    stub_mcp.STATE.reset()
    stub_mcp.STATE.docs["doc0"] = "Groww Review Pulse"
    return load_mcp_config(APPEND_CONFIG)


@pytest.fixture
def append_factory(append_config: McpConfig) -> GatewayFactory:
    return stub_factory(append_config)
