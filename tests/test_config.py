import re
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from pulse.config import Settings, load_settings

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "config" / "pulse.yaml"


def write_config(tmp_path: Path, mutate: Callable[[dict], None]) -> Path:
    config = yaml.safe_load(CONFIG.read_text())
    mutate(config)
    path = tmp_path / "pulse.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


def test_loads_default_config() -> None:
    settings = load_settings(CONFIG)
    assert settings.window.weeks == 12 and settings.window.timezone == "Asia/Kolkata"
    themes = settings.themes
    assert (themes.min, themes.max, themes.top_n, themes.name_max_words) == (3, 5, 3, 4)
    assert settings.pulse.max_words == 250
    llm = settings.llm
    assert (llm.provider, llm.model, llm.api_key_env) == ("groq", "openai/gpt-oss-120b", "GROQ_API_KEY")
    assert settings.judge_model == "qwen/qwen3.8-27b"
    assert (llm.limits.tpm, llm.limits.tpd, llm.daily_token_reserve) == (8000, 200000, 20000)
    assert llm.stages["classify"].effort == "low" and llm.pricing.input == 0.0


def test_unmodified_roundtrip_loads(tmp_path: Path) -> None:
    """Control for the rejection tests below: the YAML round-trip alone must not break loading."""
    assert load_settings(write_config(tmp_path, lambda config: None)).window.weeks == 12


@pytest.mark.parametrize("weeks", [7, 13])
def test_window_weeks_must_be_8_to_12(tmp_path: Path, weeks: int) -> None:
    path = write_config(tmp_path, lambda config: config["window"].update(weeks=weeks))
    with pytest.raises(ValidationError):
        load_settings(path)


def test_themes_min_must_cover_top_n(tmp_path: Path) -> None:
    path = write_config(tmp_path, lambda config: config["themes"].update(min=2))
    with pytest.raises(ValidationError):
        load_settings(path)


def test_themes_min_must_not_exceed_max(tmp_path: Path) -> None:
    path = write_config(tmp_path, lambda config: config["themes"].update(min=6))
    with pytest.raises(ValidationError, match="top_n <= min <= max"):
        load_settings(path)


def test_missing_llm_stage_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, lambda config: config["llm"]["stages"].pop("compose"))
    with pytest.raises(ValidationError, match="compose"):
        load_settings(path)


def test_unknown_llm_stage_is_rejected(tmp_path: Path) -> None:
    typo_stage = {"clasify": {"effort": "low", "max_tokens": 10}}
    path = write_config(tmp_path, lambda config: config["llm"]["stages"].update(typo_stage))
    with pytest.raises(ValidationError, match="clasify"):
        load_settings(path)


@pytest.mark.parametrize(
    "case",
    [
        ("themes", "rediscover_unthemed_threshold", 20),
        ("themes", "discovery_sample", 0),
        ("themes", "name_max_words", 0),
        ("llm", "classify_chunk_size", 0),
        ("llm", "daily_token_reserve", -1),
        ("monitoring", "daily_tokens_warn", 5),
        ("pulse", "max_words", -250),
        ("pulse", "retries", -1),
        ("monitoring", "skipped_share_warn", 5),
        ("evals", "judge_majority_flip_rate", 5),
        ("retention", "raw_days", 0),
    ],
)
def test_out_of_range_tunables_are_rejected(tmp_path: Path, case: tuple[str, str, float]) -> None:
    section, key, bad_value = case
    path = write_config(tmp_path, lambda config: config[section].update({key: bad_value}))
    with pytest.raises(ValidationError, match=key):
        load_settings(path)


@pytest.mark.parametrize("case", [("input", -4), ("output", -1)])
def test_out_of_range_pricing_is_rejected(tmp_path: Path, case: tuple[str, float]) -> None:
    key, bad_value = case
    path = write_config(tmp_path, lambda config: config["llm"]["pricing"].update({key: bad_value}))
    with pytest.raises(ValidationError, match=key):
        load_settings(path)


def test_numeric_app_store_id_loads_as_string(tmp_path: Path) -> None:
    path = write_config(tmp_path, lambda config: config["product"].update(app_store_id=1404871703))
    assert load_settings(path).product.app_store_id == "1404871703"


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, lambda config: config["pulse"].update(max_wrods=250))
    with pytest.raises(ValidationError, match="max_wrods"):
        load_settings(path)


# google-play-scraper reads public Play reviews (Task 1.4); it is not a Google API client.
ALLOWED_GOOGLE_PACKAGES = {"google-play-scraper"}


def test_no_google_api_client_dependency() -> None:
    deps = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["dependencies"]
    names = [re.split(r"[<>=!~;\[ ]", dep, maxsplit=1)[0].lower() for dep in deps]
    google = [name for name in names if name.startswith(("google-", "googleapis"))]
    assert set(google) <= ALLOWED_GOOGLE_PACKAGES


def test_play_store_source_settings(settings: Settings) -> None:
    play = settings.sources.play_store
    assert play.enabled and play.max_reviews == 20000 and play.lang == "en"


def test_ingest_settings_defaults(settings: Settings) -> None:
    assert settings.ingest.min_words == 8 and settings.ingest.languages == ["en", "hinglish"]


@pytest.mark.parametrize("languages", [["fr"], []])
def test_ingest_settings_reject_unknown_language(tmp_path: Path, languages: list[str]) -> None:
    path = write_config(tmp_path, lambda config: config["ingest"].update(languages=languages))
    with pytest.raises(ValidationError, match="languages"):
        load_settings(path)


def test_stage_max_tokens_must_fit_the_per_minute_limit(tmp_path: Path) -> None:  # LLM-12
    path = write_config(tmp_path, lambda config: config["llm"]["stages"]["discover"].update(max_tokens=9000))
    with pytest.raises(ValidationError, match="tpm"):
        load_settings(path)


def test_daily_reserve_must_be_below_the_daily_limit(tmp_path: Path) -> None:
    path = write_config(tmp_path, lambda config: config["llm"].update(daily_token_reserve=200000))
    with pytest.raises(ValidationError, match="daily_token_reserve"):
        load_settings(path)


def test_effort_rejects_levels_groq_does_not_offer(tmp_path: Path) -> None:
    path = write_config(tmp_path, lambda config: config["llm"]["stages"]["compose"].update(effort="max"))
    with pytest.raises(ValidationError, match="effort"):
        load_settings(path)


def test_discovery_settings(settings: Settings) -> None:  # D3, LLM-12
    themes = settings.themes
    assert themes.discovery_batch == 100 and themes.discovery_sample == 400
    assert "customer support" in themes.seed_hints and "KYC and onboarding" in themes.seed_hints
