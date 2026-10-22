from datetime import UTC, datetime

from factories import make_classification, make_review
from fakes import FakeLLM
from pydantic import BaseModel

from pulse.analysis.aggregate import Aggregates, ThemeStats
from pulse.llm.compose import PulseDraft, compose_pulse, evidence_sample
from pulse.llm.prompts import DATA_RULE
from pulse.llm.schema import LLMRequest
from pulse.models import Quote, Review, Taxonomy, Theme
from pulse.timeutil import window_for_week

WINDOW = window_for_week("2026-W42", 12, "Asia/Kolkata")
THEMES = ("withdrawals", "charges", "kyc")
RULES = (
    "Write exactly one summary per theme, in the given order.",
    "Write exactly 3 action ideas; each names a concrete product, support or communication change "
    "and lists the theme_ids it addresses.",
    "Summaries: at most 18 words. Actions: at most 20 words. Rationale: at most 25 words.",
    "Do not write any numbers; statistics are added by the system.",
    "Cover at least two of the three themes across the actions.",
)


def stats(theme_id: str, count: int) -> ThemeStats:
    return ThemeStats(
        theme_id=theme_id,
        name=theme_id.title(),
        count=count,
        share=0.3,
        mean_rating=2.0,
        low_rating_share=0.6,
        sentiment={"negative": count},
        latest_week_share=0.3,
        trend_pts=0,
        trend_significant=False,
        sources={"play_store": count},
        low_sample=False,
    )


def taxonomy() -> Taxonomy:
    themes = [
        Theme(
            id=slug, name=slug.title(), definition=f"Reviews about {slug}", include_hints=[], exclude_hints=[]
        )
        for slug in THEMES
    ]
    return Taxonomy(
        version=1,
        themes=themes,
        created_from_run="r",
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
        sample_review_ids=[],
    )


def aggregates(top: list[ThemeStats]) -> Aggregates:
    return Aggregates(
        window=WINDOW,
        window_total=300,
        window_mean_rating=3.1,
        excluded={},
        total=200,
        mean_rating=2.9,
        sources={"play_store": 300},
        unthemed_count=20,
        unthemed_share=0.1,
        actionable_unthemed_share=0.1,
        latest_week_total=40,
        duplicate_share=0.0,
        unclassified_count=0,
        themes=top,
    )


def draft_handler(req: LLMRequest) -> BaseModel:
    return req.schema.model_validate(
        {
            "themes": [{"theme_id": theme, "summary": f"Users report {theme} problems."} for theme in THEMES],
            "actions": [
                {"theme_ids": [theme], "action": f"Fix {theme} flow.", "rationale": "Many users ask."}
                for theme in THEMES
            ],
        }
    )


def compose(feedback: list[str] | None = None) -> tuple[PulseDraft, FakeLLM]:
    top = [stats(theme, 30 - index) for index, theme in enumerate(THEMES)]
    quotes = [
        Quote(
            review_id=f"{theme}0",
            theme_id=theme,
            text="It is stuck.",
            rating=1,
            source="play_store",
            month="Oct 2026",
        )
        for theme in THEMES
    ]
    evidence = {
        theme: [make_review(review_id=f"{theme}0", text=f"The {theme} part is broken")] for theme in THEMES
    }
    llm = FakeLLM({"compose": draft_handler})
    draft = compose_pulse(llm, top, aggregates(top), quotes, evidence, taxonomy(), feedback=feedback)
    return draft, llm


def test_compose_prompt_has_rules_order_and_data_rule() -> None:
    draft, llm = compose()
    request = llm.calls[0]
    assert DATA_RULE in request.system and all(rule in request.system for rule in RULES)
    positions = [request.user.index(f'"theme_id": "{theme}"') for theme in THEMES]
    assert positions == sorted(positions)
    assert "Reviews about charges" in request.user and "It is stuck." in request.user
    assert [summary.theme_id for summary in draft.themes] == list(THEMES) and len(draft.actions) == 3


def test_feedback_is_appended() -> None:
    _, llm = compose(feedback=["V5: summary for kyc has 19 words (max 18)"])
    assert llm.calls[0].user.endswith(
        "Fix these problems from your previous draft:\n- V5: summary for kyc has 19 words (max 18)"
    )
    _, plain = compose()
    assert "Fix these problems" not in plain.calls[0].user


def members(count: int) -> tuple[list[Review], dict]:
    reviews = [make_review(review_id=f"w{index:03d}") for index in range(count)]
    reviews += [make_review(review_id=f"c{index:03d}") for index in range(count)]
    labels = {
        item.review_id: make_classification(
            item.review_id, "withdrawals" if item.review_id[0] == "w" else "charges"
        )
        for item in reviews
    }
    return reviews, labels


def test_evidence_sample_is_seeded_and_theme_scoped() -> None:
    reviews, labels = members(50)
    sample = evidence_sample("withdrawals", reviews, labels, 20, seed=1)
    assert len(sample) == 20 and all(item.review_id.startswith("w") for item in sample)
    assert sample == evidence_sample("withdrawals", reviews, labels, 20, seed=1)
    assert sample != evidence_sample("withdrawals", reviews, labels, 20, seed=2)
    assert len(evidence_sample("charges", *members(5), 20)) == 5
