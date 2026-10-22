import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from factories import make_classification, make_review
from fakes import FakeLLM
from pydantic import BaseModel

from pulse.analysis.aggregate import ThemeStats
from pulse.config import PulseSettings
from pulse.llm.prompts import DATA_RULE
from pulse.llm.quotes import (
    QuoteUnavailable,
    candidate_pool,
    first_sentence,
    select_quotes,
    verify_quote,
)
from pulse.llm.schema import LLMRequest
from pulse.models import Classification, Review
from pulse.timeutil import window_for_week

WINDOW = window_for_week("2026-W42", 12, "Asia/Kolkata")  # latest week: 12–18 Oct 2026 IST
LATEST = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
LIMITS = PulseSettings()
TEXT = "Support is not replying\nat all. Withdrawal stuck for 3 days."


def stats(theme_id: str) -> ThemeStats:
    return ThemeStats(
        theme_id=theme_id,
        name=theme_id.title(),
        count=10,
        share=0.3,
        mean_rating=2.0,
        low_rating_share=0.6,
        sentiment={"negative": 10},
        latest_week_share=0.3,
        trend_pts=0,
        trend_significant=False,
        sources={"play_store": 10},
        low_sample=False,
    )


def review(review_id: str, text: str, **fields: object) -> Review:
    defaults: dict[str, object] = {"word_count": len(text.split()), "posted_at": LATEST}
    return make_review(review_id=review_id, text=text, **(defaults | fields))


def labelled(theme_reviews: dict[str, list[Review]]) -> tuple[list[Review], dict[str, Classification]]:
    reviews = [item for members in theme_reviews.values() for item in members]
    labels = {
        item.review_id: make_classification(item.review_id, theme)
        for theme, members in theme_reviews.items()
        for item in members
    }
    return reviews, labels


def pool_items(req: LLMRequest) -> list[dict]:
    return json.loads(req.user[req.user.index("<reviews>") + len("<reviews>") : req.user.index("</reviews>")])


def pick_first_words(count: int = 6) -> Callable:
    def handle(req: LLMRequest) -> BaseModel:
        first = pool_items(req)[0]
        return req.schema.model_validate({"k": first["k"], "quote": " ".join(first["text"].split()[:count])})

    return handle


@pytest.mark.parametrize(
    "case",
    [
        ("Support is not replying at all.", True),
        ('"Withdrawal stuck for 3 days."', True),
        ("Support is never replying at all.", False),
        ("", False),
        (" ".join(["word"] * 31), False),
    ],
)
def test_verify_quote(case: tuple[str, bool]) -> None:  # QUO-1, QUO-2, QUO-9
    quote, expected = case
    text = TEXT + " " + " ".join(["word"] * 31)
    assert verify_quote(quote, text) is expected


def test_verify_quote_normalises_curly_apostrophes() -> None:  # QUO-8
    assert verify_quote("Can't withdraw", "I can’t withdraw. Can’t withdraw anything.")


def test_first_sentence_stops_at_terminal_punctuation() -> None:
    assert first_sentence("Money stuck for days! Nobody answers.") == "Money stuck for days!"
    assert first_sentence("no punctuation here at all") == "no punctuation here at all"


def test_candidate_pool_filters() -> None:  # QUO-5, ING-11, PII-9
    words = "the withdrawal has been stuck for many days now and support is silent"
    older = review("older", words, posted_at=LATEST - timedelta(days=21))
    latest = review("latest", words)
    rejected = [
        review("short", "withdrawal stuck"),
        review("long", " ".join(["stuck"] * 61)),
        review("dup", words, is_duplicate_text=True),
        review("hindi", words, language="hi"),
        review("upi", words + " pay me at [upi]"),
    ]
    other_theme = review("other", words)
    reviews, labels = labelled({"withdrawals": [older, latest, *rejected], "charges": [other_theme]})
    pool = candidate_pool("withdrawals", reviews, labels, WINDOW, LIMITS)
    assert [item.review_id for item in pool] == ["latest", "older"]


def test_candidate_pool_keeps_at_most_quote_candidates() -> None:
    members = [
        review(f"r{index:03d}", f"review number {index} says the app is slow to load") for index in range(40)
    ]
    reviews, labels = labelled({"performance": members})
    pool = candidate_pool("performance", reviews, labels, WINDOW, LIMITS, seed=3)
    assert len(pool) == LIMITS.quote_candidates
    assert pool == candidate_pool("performance", reviews, labels, WINDOW, LIMITS, seed=3)


def themed_reviews() -> dict[str, list[Review]]:
    return {
        theme: [
            review(f"{theme}{index}", f"{theme} problem number {index} is making this app hard to use")
            for index in range(3)
        ]
        for theme in ("withdrawals", "charges", "kyc")
    }


def test_select_quotes_happy_path() -> None:
    reviews, labels = labelled(themed_reviews())
    llm = FakeLLM({"quotes": pick_first_words()})
    top = [stats("withdrawals"), stats("charges"), stats("kyc")]
    quotes, fallbacks = select_quotes(llm, top, reviews, labels, WINDOW, LIMITS)
    assert fallbacks == 0 and len(llm.calls) == 3
    assert [quote.theme_id for quote in quotes] == ["withdrawals", "charges", "kyc"]
    assert len({quote.review_id for quote in quotes}) == 3
    assert all(verify_quote(quote.text, labels_text(reviews, quote.review_id)) for quote in quotes)
    assert quotes[0].month == "Oct 2026" and quotes[0].rating == 3 and quotes[0].source == "play_store"


def labels_text(reviews: list[Review], review_id: str) -> str:
    return next(item.text for item in reviews if item.review_id == review_id)


def test_invalid_picks_retry_then_fall_back_to_first_sentence() -> None:  # QUO-1
    first = review("w1", "Withdrawal is stuck since Monday. Support never replies to my emails at all.")
    reviews, labels = labelled({"withdrawals": [first]})

    def paraphrase(req: LLMRequest) -> BaseModel:
        return req.schema.model_validate({"k": 1, "quote": "Withdrawal has been stuck since Monday."})

    llm = FakeLLM({"quotes": paraphrase})
    quotes, fallbacks = select_quotes(llm, [stats("withdrawals")], reviews, labels, WINDOW, LIMITS)
    assert len(llm.calls) == 1 + LIMITS.retries and fallbacks == 1
    assert quotes[0].text == "Withdrawal is stuck since Monday."
    assert "not a verbatim span" in llm.calls[1].user


def test_pick_outside_pool_is_rejected() -> None:  # QUO-4
    reviews, labels = labelled({"withdrawals": themed_reviews()["withdrawals"]})
    answers = iter([{"k": 99, "quote": "withdrawals problem number"}])

    def handle(req: LLMRequest) -> BaseModel:
        answer = next(answers, None)
        return req.schema.model_validate(answer) if answer else pick_first_words()(req)

    llm = FakeLLM({"quotes": handle})
    quotes, fallbacks = select_quotes(llm, [stats("withdrawals")], reviews, labels, WINDOW, LIMITS)
    assert len(llm.calls) == 2 and fallbacks == 0 and quotes[0].review_id.startswith("withdrawals")
    assert "k=99 is not one of the reviews" in llm.calls[1].user


def test_empty_pool_raises_quote_unavailable() -> None:  # QUO-3
    reviews, labels = labelled({"withdrawals": [review("short", "stuck again")]})
    with pytest.raises(QuoteUnavailable) as raised:
        select_quotes(
            FakeLLM({"quotes": pick_first_words()}), [stats("withdrawals")], reviews, labels, WINDOW, LIMITS
        )
    assert raised.value.theme_id == "withdrawals"


def test_quote_month_uses_ist() -> None:  # QUO-10
    late = review(
        "late",
        "withdrawal problem is making this app very hard to use daily",
        posted_at=datetime(2026, 9, 30, 20, 0, tzinfo=UTC),  # 1 Oct 01:30 IST
    )
    reviews, labels = labelled({"withdrawals": [late]})
    quotes, _ = select_quotes(
        FakeLLM({"quotes": pick_first_words()}), [stats("withdrawals")], reviews, labels, WINDOW, LIMITS
    )
    assert quotes[0].month == "Oct 2026"


def test_quotes_prompt_has_rules() -> None:  # QUO-7
    reviews, labels = labelled({"withdrawals": themed_reviews()["withdrawals"]})
    llm = FakeLLM({"quotes": pick_first_words()})
    select_quotes(llm, [stats("withdrawals")], reviews, labels, WINDOW, LIMITS)
    system = llm.calls[0].system
    assert DATA_RULE in system and "Avoid profanity and personal attacks." in system
