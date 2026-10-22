import os
import subprocess
from pathlib import Path

import pytest

from pulse.config import load_env

REPO = Path(__file__).resolve().parents[1]


def test_dotenv_fills_missing_variables_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("GROQ_API_KEY=from-file\nPULSE_TEST_SETTING=from-file\n")
    monkeypatch.setenv("GROQ_API_KEY", "placeholder")
    monkeypatch.delenv("GROQ_API_KEY")
    monkeypatch.setenv("PULSE_TEST_SETTING", "from-environment")
    load_env(env_file)
    assert os.environ["GROQ_API_KEY"] == "from-file"
    assert os.environ["PULSE_TEST_SETTING"] == "from-environment"


def test_missing_dotenv_is_not_an_error(tmp_path: Path) -> None:
    load_env(tmp_path / "absent.env")


def test_env_example_lists_keys_and_env_is_git_ignored() -> None:
    example = (REPO / ".env.example").read_text()
    assert "GROQ_API_KEY=" in example
    assert "GROQ_API_KEY=gsk_" not in example  # the template never carries a real key
    ignored = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=REPO, check=False)
    assert ignored.returncode == 0


def test_env_example_lists_every_mcp_server_variable() -> None:  # CLAUDE.md: every name, empty value
    from pulse.deliver.mcp_client import load_mcp_config

    lines = (REPO / ".env.example").read_text().splitlines()
    server = load_mcp_config(REPO / "config" / "mcp.yaml").server
    for name in [*server.env, *filter(None, [server.auth_token_env])]:
        assert f"{name}=" in lines, name
