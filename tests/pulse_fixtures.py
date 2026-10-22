"""A complete, valid pulse input for render and validate tests (Task 4.3); tests mutate a fresh copy."""

from dataclasses import dataclass
from datetime import UTC, datetime

from factories import make_classification, make_review

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
    reviews = {
        f"{theme_id}0": make_review(review_id=f"{theme_id}0", text=text, rating=1)
        for theme_id, text in REVIEW_TEXTS.items()
    }
    return PulseInputs(
        agg=aggregates(),
        top=[stats(*theme) for theme in THEMES],
        draft=valid_draft(),
        quotes=valid_quotes(),
        taxonomy=valid_taxonomy(),
        reviews=reviews,
        labels={review_id: make_classification(review_id, review_id[:-1]) for review_id in reviews},
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
