from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import GatewayFactory
from test_cli import invoke

from pulse import cli
from pulse.cli import Deps, doctor_checks
from pulse.config import Settings
from pulse.deliver.mcp_client import McpConfig

MODELS = ("openai/gpt-oss-120b", "qwen/qwen3.8-27b")


def groq_with(*model_ids: str) -> SimpleNamespace:
    """A stand-in for GroqLLM: only `client.models.list()` is used by the doctor."""
    listing = SimpleNamespace(data=[SimpleNamespace(id=model_id) for model_id in model_ids])
    return SimpleNamespace(client=SimpleNamespace(models=SimpleNamespace(list=lambda: listing)))


def configured(settings: Settings) -> Settings:
    real = {"recipient": "me@example.com", "drive_folder_id": "1AbCdEf"}
    delivery = settings.delivery.model_copy(update=real)
    return settings.model_copy(update={"delivery": delivery})


def results(settings: Settings, deps: Deps) -> dict[str, tuple[bool, str]]:
    return {check.name: (check.ok, check.detail) for check in doctor_checks(settings, deps)}


def test_doctor_all_ok_with_stub(settings: Settings, gateway_factory: GatewayFactory) -> None:
    deps = Deps(store=None, llm=groq_with(*MODELS), gateway_factory=gateway_factory)
    found = results(configured(settings), deps)
    assert set(found) == {"config", "delivery_config", "groq_auth", "mcp_tools"}
    assert all(ok for ok, _ in found.values()), found


def test_doctor_fails_on_placeholder_config(
    settings: Settings, gateway_factory: GatewayFactory
) -> None:  # DEL-10
    deps = Deps(store=None, llm=groq_with(*MODELS), gateway_factory=gateway_factory)
    ok, detail = results(settings, deps)["delivery_config"]
    assert not ok and "recipient" in detail and "drive_folder_id" in detail


def test_doctor_fails_when_a_model_is_missing(
    settings: Settings, gateway_factory: GatewayFactory
) -> None:  # LLM-14
    deps = Deps(store=None, llm=groq_with("openai/gpt-oss-120b"), gateway_factory=gateway_factory)
    ok, detail = results(configured(settings), deps)["groq_auth"]
    assert not ok and "qwen/qwen3.8-27b" in detail


def test_doctor_fails_without_groq_key(
    tmp_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # OPS-10, LLM-14
    monkeypatch.setattr(cli, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(cli, "MCP_CONFIG", tmp_path / "absent-mcp.yaml")
    result = invoke(tmp_settings, tmp_path, "doctor")
    assert result.exit_code == 1 and "groq_auth FAIL" in result.output and "GROQ_API_KEY" in result.output


def test_doctor_reports_malformed_mcp_config(
    tmp_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # DEL-1
    broken = tmp_path / "mcp.yaml"
    broken.write_text("server: {transport: stdio}\noperations: {}\n")
    monkeypatch.setattr(cli, "MCP_CONFIG", broken)
    monkeypatch.setattr(cli, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    result = invoke(tmp_settings, tmp_path, "doctor")
    assert result.exit_code == 1 and "mcp_tools FAIL" in result.output and "allowlist" in result.output
    assert "Traceback" not in result.output


def test_doctor_checks_the_running_doc_for_an_append_server(
    settings: Settings, append_factory: GatewayFactory, append_config: McpConfig
) -> None:  # DEL-10, DEL-15
    deps = Deps(store=None, llm=groq_with(*MODELS), gateway_factory=append_factory, mcp_config=append_config)
    ok, detail = results(settings, deps)["delivery_config"]
    assert not ok and "doc_id" in detail and "recipient" not in detail
    real = settings.delivery.model_copy(update={"doc_id": "1AbCdEf"})
    found = results(settings.model_copy(update={"delivery": real}), deps)
    assert found["delivery_config"][0] and "no Gmail draft" in found["delivery_config"][1]
    assert found["mcp_tools"][0], found["mcp_tools"]
