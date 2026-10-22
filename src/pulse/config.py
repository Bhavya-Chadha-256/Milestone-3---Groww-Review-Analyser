"""Typed settings loaded from config/pulse.yaml (architecture.md §10)."""

from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from annotated_types import Ge as AtLeast
from annotated_types import Gt as Above
from annotated_types import Le as AtMost
from annotated_types import MinLen
from dotenv import load_dotenv
from pydantic import (
    BaseModel,
    ConfigDict,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    model_validator,
)

from pulse.models import Language

Share = Annotated[float, Above(0), AtMost(1)]
"""A fraction in (0, 1]. Rejects percent-style typos such as 20 for 0.20."""

REQUIRED_STAGES = ("discover", "classify", "quotes", "compose", "judge")


class StrictModel(BaseModel):
    """Base for settings sections: unknown keys are a config error, not silently ignored."""

    model_config = ConfigDict(extra="forbid")


class ProductSettings(StrictModel):
    # The real iOS app ID is numeric; accept it unquoted in YAML.
    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    name: str
    play_package: str
    app_store_id: str
    storefront: str = "in"


class WindowSettings(StrictModel):
    weeks: Annotated[int, AtLeast(8), AtMost(12)] = 12
    timezone: str = "Asia/Kolkata"


class SourceToggle(StrictModel):
    enabled: bool = False


class PlaySourceSettings(StrictModel):
    """`pulse fetch play-store` (google-play-scraper). Country comes from product.storefront."""

    enabled: bool = False
    max_reviews: PositiveInt = 20000
    lang: str = "en"


class SourcesSettings(StrictModel):
    file: SourceToggle = SourceToggle(enabled=True)
    app_store: SourceToggle = SourceToggle(enabled=False)
    play_store: PlaySourceSettings = PlaySourceSettings()


class ThemesSettings(StrictModel):
    min: PositiveInt = 3
    max: PositiveInt = 5
    top_n: PositiveInt = 3
    name_max_words: PositiveInt = 4
    min_reviews: PositiveInt = 100
    discovery_sample: PositiveInt = 400
    rediscover_unthemed_threshold: Share = 0.20
    seed_hints: list[str] = []

    @model_validator(mode="after")
    def min_covers_top_n(self) -> Self:
        if not self.top_n <= self.min <= self.max:
            raise ValueError(
                "themes must satisfy top_n <= min <= max, "
                f"got top_n={self.top_n}, min={self.min}, max={self.max}"
            )
        return self


Effort = Literal["low", "medium", "high"]  # Groq reasoning_effort levels


class StageConfig(StrictModel):
    effort: Effort
    max_tokens: PositiveInt  # max_completion_tokens; must fit within limits.tpm


class Pricing(StrictModel):
    """USD per million tokens; 0 on Groq's free tier."""

    input: NonNegativeFloat = 0.0
    output: NonNegativeFloat = 0.0


class RateLimits(StrictModel):
    """Per-model limits of the provider plan (Groq free plan; verify at console.groq.com/settings/limits)."""

    rpm: PositiveInt = 30
    tpm: PositiveInt = 8000
    rpd: PositiveInt = 1000
    tpd: PositiveInt = 200000


class LLMSettings(StrictModel):
    provider: Literal["groq"] = "groq"
    model: str
    api_key_env: str = "GROQ_API_KEY"
    classify_chunk_size: PositiveInt = 50
    limits: RateLimits = RateLimits()
    daily_token_reserve: NonNegativeInt = 20000  # kept back from classification for later stages
    stages: dict[str, StageConfig]
    pricing: Pricing = Pricing()

    @model_validator(mode="after")
    def stages_are_exactly_required(self) -> Self:
        missing = [name for name in REQUIRED_STAGES if name not in self.stages]
        unknown = sorted(set(self.stages) - set(REQUIRED_STAGES))
        if missing or unknown:
            raise ValueError(
                f"llm.stages must be exactly {REQUIRED_STAGES}; missing={missing}, unknown={unknown}"
            )
        return self

    @model_validator(mode="after")
    def requests_fit_the_limits(self) -> Self:
        too_big = sorted(name for name, stage in self.stages.items() if stage.max_tokens >= self.limits.tpm)
        if too_big:
            raise ValueError(f"llm.stages {too_big}: max_tokens must be below limits.tpm ({self.limits.tpm})")
        if self.daily_token_reserve >= self.limits.tpd:
            raise ValueError(f"llm.daily_token_reserve must be below limits.tpd ({self.limits.tpd})")
        return self


class PulseSettings(StrictModel):
    max_words: PositiveInt = 250
    quotes: PositiveInt = 3
    actions: PositiveInt = 3
    summary_max_words: PositiveInt = 18
    action_max_words: PositiveInt = 20
    quote_max_words: PositiveInt = 30
    rationale_max_words: PositiveInt = 25
    quote_min_words: PositiveInt = 8
    quote_pool_max_words: PositiveInt = 60
    quote_candidates: PositiveInt = 30
    evidence_per_theme: PositiveInt = 20
    retries: NonNegativeInt = 2
    min_theme_reviews: PositiveInt = 10


class MonitoringSettings(StrictModel):
    duplicate_share_warn: Share = 0.10
    volume_change_warn: Share = 0.5
    skipped_share_warn: Share = 0.05
    unclassified_warn: Share = 0.01
    cost_spike_factor: PositiveFloat = 2.0
    retries_warn: NonNegativeInt = 1
    daily_tokens_warn: Share = 0.80  # M-COST-2: share of a model's daily token limit


class EvalSettings(StrictModel):
    judge_model: str | None = None
    judge_majority_flip_rate: Share = 0.05


class DeliverySettings(StrictModel):
    drive_folder_id: str
    recipient: str
    doc_title: str


class IngestSettings(StrictModel):
    """Theme eligibility (architecture.md §3.2): other reviews are stored but never themed or quoted."""

    min_words: PositiveInt = 8
    languages: Annotated[list[Language], MinLen(1)] = ["en", "hinglish"]


class PrivacySettings(StrictModel):
    extra_patterns: list[str] = []


class RetentionSettings(StrictModel):
    raw_days: PositiveInt = 14


class PathSettings(StrictModel):
    db: Path
    runs: Path
    raw: Path
    mappings: Path


class Settings(StrictModel):
    product: ProductSettings
    window: WindowSettings
    sources: SourcesSettings
    themes: ThemesSettings
    llm: LLMSettings
    pulse: PulseSettings
    monitoring: MonitoringSettings
    evals: EvalSettings
    delivery: DeliverySettings
    ingest: IngestSettings
    privacy: PrivacySettings
    retention: RetentionSettings
    paths: PathSettings

    @property
    def judge_model(self) -> str:
        """Eval judges use the pipeline model unless evals.judge_model overrides it."""
        return self.evals.judge_model or self.llm.model


def load_env(path: Path) -> None:
    """Load secrets such as GROQ_API_KEY from a git-ignored .env file; real environment variables win."""
    load_dotenv(Path(path), override=False)


def load_settings(path: Path) -> Settings:
    return Settings.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
