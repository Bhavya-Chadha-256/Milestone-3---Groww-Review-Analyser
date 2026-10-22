"""Shared pytest fixtures for every task (implementation.md Task 0.2)."""

import shutil
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

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


# Dashboard (Phase 7): the API over the sample store in tests/web_data.py, as seen from 22 Oct 2026.

DASHBOARD_TODAY = date(2026, 10, 22)
DASHBOARD_URL = "http://127.0.0.1:8787"


def dashboard_client(settings: Settings, **hooks: Any) -> Any:
    """A TestClient for `create_app(settings)`; the web extra is imported only by dashboard tests.
    Delivery readiness defaults to "ready, no draft tool", so no test reaches a real MCP server."""
    from fastapi.testclient import TestClient

    from pulse.web.app import ServerHooks, create_app
    from pulse.web.pulse_view import Readiness

    hooks = {"readiness": lambda: Readiness(problems=[], draft_supported=False)} | hooks
    app = create_app(settings, today=DASHBOARD_TODAY, hooks=ServerHooks(**hooks))
    return TestClient(app, base_url=DASHBOARD_URL)


@pytest.fixture
def web_client(tmp_settings: Settings) -> Any:
    from web_data import write

    write(Path(tmp_settings.paths.db))
    return dashboard_client(tmp_settings)
