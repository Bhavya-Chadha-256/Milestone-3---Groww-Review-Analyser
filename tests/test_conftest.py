from pathlib import Path

from pulse.config import Settings


def test_settings_fixture_loads_repo_config(settings: Settings) -> None:
    assert settings.llm.model == "openai/gpt-oss-120b"


def test_tmp_settings_paths_are_isolated(tmp_settings: Settings, tmp_path: Path) -> None:
    paths = tmp_settings.paths
    for path in (paths.db, paths.runs, paths.raw, paths.mappings):
        assert Path(path).resolve().is_relative_to(tmp_path.resolve())
    assert Path(paths.mappings).is_dir()


def test_tmp_settings_does_not_mutate_settings(settings: Settings, tmp_settings: Settings) -> None:
    assert settings.paths.db != tmp_settings.paths.db


def test_fixtures_dir_is_under_repo_root(repo_root: Path, fixtures_dir: Path) -> None:
    assert (repo_root / "pyproject.toml").is_file()
    assert fixtures_dir == repo_root / "tests" / "fixtures"
