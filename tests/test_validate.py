from collections.abc import Callable

import pytest
from pulse_fixtures import PulseInputs, valid_inputs

from pulse.report.validate import ValidationIssue, route

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


def test_valid_pulse_passes() -> None:
    report = valid_inputs().check()
    assert report.passed and report.issues == []


@pytest.mark.parametrize(
    "case",
    [
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
    ],
    ids=lambda case: case[0].__name__,
)
def test_each_gate_fails(
    case: tuple[Callable[[PulseInputs], None], str],
) -> None:  # PII-12, REN-2, REN-3, REN-10
    mutate, code = case
    inputs = valid_inputs()
    mutate(inputs)
    report = inputs.check()
    assert not report.passed and code in {issue.code for issue in report.issues}


def test_v5_message_names_field_and_counts() -> None:
    inputs = valid_inputs()
    summary_19_words(inputs)
    messages = [issue.message for issue in inputs.check().issues if issue.code == "V5"]
    assert messages == ["summary for withdrawals has 19 words (max 18)"]


def test_taxonomy_over_five_themes_fails_v2() -> None:
    inputs = valid_inputs()
    extra = inputs.taxonomy.themes[0]
    inputs.taxonomy.themes += [extra.model_copy(update={"id": f"extra{index}"}) for index in range(3)]
    assert "V2" in {issue.code for issue in inputs.check().issues}


@pytest.mark.parametrize("text", ["Enable 2FA prompts", "Explain T+1 settlement in app"])
def test_v7_allows_alphanumeric_tokens(text: str) -> None:  # REN-5
    inputs = valid_inputs()
    inputs.draft.actions[0].action = text
    assert inputs.check().passed


@pytest.mark.parametrize(
    "case", [(["V3", "V5"], "quotes"), (["V6"], "quotes"), (["V5"], "compose"), ([], None)]
)
def test_route(case: tuple[list[str], str | None]) -> None:
    codes, expected = case
    assert route([ValidationIssue(code, "x") for code in codes]) == expected
