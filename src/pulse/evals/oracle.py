"""Oracle and null baselines for the validators (evaluation §5 rule 7, Tasks 4.3 and 4.5).

`valid_inputs()` is a complete pulse that must pass every gate (it renders to tests/fixtures/pulse_golden.md);
each function in `MUTATIONS` breaks exactly one gate. Unit tests and the deterministic suite share them.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from pulse.analysis.aggregate import Aggregates, ThemeStats
from pulse.config import PulseSettings
from pulse.llm.compose import ActionIdea, PulseDraft, ThemeSummary
from pulse.models import Classification, Quote, Review, Taxonomy, Theme
from pulse.report.render import PulseContext, build_context, render_markdown
from pulse.report.validate import ValidationReport, validate
from pulse.timeutil import Window, window_for_week

WINDOW = window_for_week("2026-W42", 12, "Asia/Kolkata")
THEMES = (
    ("withdrawals", "Withdrawals", 0.30, 4, True),
    ("charges", "Charges & Fees", 0.25, -3, True),
    ("kyc", "KYC Verification", 0.20, 1, False),
)
REVIEW_TEXTS = {
    "withdrawals": "Withdrawal has been stuck for three days and support keeps sending the same reply.",
    "charges": "Brokerage charges are not shown clearly before I place an order on this app.",
    "kyc": "My KYC is still pending after uploading every document twice, very frustrating experience.",
}
QUOTES = {
    "withdrawals": "Withdrawal has been stuck for three days",
    "charges": "Brokerage charges are not shown clearly before I place an order",
    "kyc": "My KYC is still pending after uploading every document twice",
}


@dataclass
class PulseInputs:
    agg: Aggregates
    top: list[ThemeStats]
    draft: PulseDraft
    quotes: list[Quote]
    taxonomy: Taxonomy
    reviews: dict[str, Review]
    labels: dict[str, Classification]
    limits: PulseSettings

    def context(self) -> PulseContext:
        return build_context(self.agg, self.top, self.draft, self.quotes, self.taxonomy)

    def markdown(self) -> str:
        return render_markdown(self.context())

    def check(self, markdown: str | None = None) -> ValidationReport:
        ctx = self.context()
        return validate(
            ctx,
            markdown if markdown is not None else render_markdown(ctx),
            [stats.theme_id for stats in self.top],
            self.taxonomy,
            self.reviews,
            self.labels,
            self.limits,
        )


def stats(  # craftsman-ignore: WARN-PY001 (one THEMES row unpacked)
    theme_id: str, name: str, share: float, trend: int | None, significant: bool
) -> ThemeStats:
    return ThemeStats(
        theme_id=theme_id,
        name=name,
        count=round(share * 200),
        share=share,
        mean_rating=2.1,
        low_rating_share=0.6,
        sentiment={"negative": 10},
        latest_week_share=share,
        trend_pts=trend,
        trend_significant=significant,
        sources={"play_store": 10},
        low_sample=False,
    )


def aggregates(window: Window = WINDOW, sources: dict[str, int] | None = None) -> Aggregates:
    return Aggregates(
        window=window,
        window_total=1240,
        window_mean_rating=3.4,
        excluded={"too_short": 900},
        total=200,
        mean_rating=2.9,
        sources=sources or {"play_store": 1240},
        unthemed_count=30,
        unthemed_share=0.15,
        actionable_unthemed_share=0.1,
        latest_week_total=60,
        duplicate_share=0.0,
        unclassified_count=0,
        themes=[],
    )


def valid_inputs() -> PulseInputs:
    reviews = {f"{theme_id}0": _review(f"{theme_id}0", text) for theme_id, text in REVIEW_TEXTS.items()}
    return PulseInputs(
        agg=aggregates(),
        top=[stats(*theme) for theme in THEMES],
        draft=valid_draft(),
        quotes=valid_quotes(),
        taxonomy=valid_taxonomy(),
        reviews=reviews,
        labels={review_id: _label(review_id, review_id[:-1]) for review_id in reviews},
        limits=PulseSettings(),
    )


def valid_taxonomy() -> Taxonomy:
    themes = [
        Theme(id=theme_id, name=name, definition=f"About {name}", include_hints=[], exclude_hints=[])
        for theme_id, name, *_ in THEMES
    ]
    return Taxonomy(
        version=1,
        themes=themes,
        created_from_run="r",
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
        sample_review_ids=[],
    )


def valid_quotes() -> list[Quote]:
    return [
        Quote(
            review_id=f"{theme_id}0",
            theme_id=theme_id,
            text=text,
            rating=1,
            source="play_store",
            month="Oct 2026",
        )
        for theme_id, text in QUOTES.items()
    ]


def valid_draft() -> PulseDraft:
    summaries = {
        "withdrawals": "Payouts stall for days and support replies feel scripted.",
        "charges": "Users cannot see brokerage and fees before placing orders.",
        "kyc": "Verification stays pending even after users upload documents again.",
    }
    actions = [
        (
            ["withdrawals"],
            "Show live withdrawal status with an expected credit date.",
            "Users chase support because they cannot see where their money is.",
        ),
        (
            ["charges"],
            "Add a fee breakdown on the order confirmation screen.",
            "Hidden charges surprise users after the trade.",
        ),
        (
            ["kyc", "withdrawals"],
            "Send a clear reason when KYC or withdrawals are held.",
            "Silent holds drive repeat uploads and support tickets.",
        ),
    ]
    return PulseDraft(
        themes=[ThemeSummary(theme_id=theme_id, summary=summary) for theme_id, summary in summaries.items()],
        actions=[
            ActionIdea(theme_ids=theme_ids, action=action, rationale=rationale)
            for theme_ids, action, rationale in actions
        ],
    )


def _review(review_id: str, text: str) -> Review:
    return Review(
        review_id=review_id,
        source="play_store",
        rating=1,
        title=None,
        text=text,
        posted_at=datetime(2026, 10, 14, 10, 0, tzinfo=UTC),
        app_version="18.22.0",
        language="en",
        word_count=len(text.split()),
        text_hash=review_id,
    )


def _label(review_id: str, theme_id: str) -> Classification:
    return Classification(
        review_id=review_id,
        theme_id=theme_id,
        sentiment="negative",
        taxonomy_version=1,
        prompt_version="classify-v1",
    )


LONG_TEXT = " ".join(f"word{index}" for index in range(45))


def drop_one_action(inputs: PulseInputs) -> None:
    inputs.draft.actions.pop()


def swap_theme_order(inputs: PulseInputs) -> None:
    themes = inputs.draft.themes
    themes[0], themes[1] = themes[1], themes[0]


def alter_quote_word(inputs: PulseInputs) -> None:
    inputs.quotes[0].text = inputs.quotes[0].text.replace("stuck", "blocked")


def duplicate_quote_review(inputs: PulseInputs) -> None:
    inputs.quotes[1] = inputs.quotes[0].model_copy(update={"theme_id": "charges"})


def action_with_unknown_theme(inputs: PulseInputs) -> None:
    inputs.draft.actions[0].theme_ids = ["support"]


def summary_19_words(inputs: PulseInputs) -> None:
    inputs.draft.themes[0].summary = " ".join(["slow"] * 19)


def pad_note_over_250_words(inputs: PulseInputs) -> None:
    """Every field at its limit: no field is too long, but the note is."""
    for summary in inputs.draft.themes:
        summary.summary = " ".join(["delay"] * 18)
    for action in inputs.draft.actions:
        action.action = " ".join(["improve"] * 20)
    for quote in inputs.quotes:
        review = inputs.reviews[quote.review_id]
        inputs.reviews[quote.review_id] = review.model_copy(update={"text": LONG_TEXT})
        quote.text = " ".join(LONG_TEXT.split()[:30])


def quote_with_upi_placeholder(inputs: PulseInputs) -> None:
    review = inputs.reviews["withdrawals0"]
    inputs.reviews["withdrawals0"] = review.model_copy(update={"text": review.text + " Refund to [upi] now"})
    inputs.quotes[0].text = "Refund to [upi] now"


def summary_with_email(inputs: PulseInputs) -> None:
    inputs.draft.themes[0].summary = "Users ask to write to ravi.kumar@example.com for help."


def summary_with_percentage(inputs: PulseInputs) -> None:
    inputs.draft.themes[0].summary = "About 30% say payouts are late."


Mutation = Callable[[PulseInputs], None]
MUTATIONS: list[tuple[Mutation, str]] = [
    (drop_one_action, "V1"),
    (swap_theme_order, "V2"),
    (alter_quote_word, "V3"),
    (duplicate_quote_review, "V3"),
    (action_with_unknown_theme, "V4"),
    (summary_19_words, "V5"),
    (pad_note_over_250_words, "V5"),
    (quote_with_upi_placeholder, "V6"),
    (summary_with_email, "V6"),
    (summary_with_percentage, "V7"),
]
