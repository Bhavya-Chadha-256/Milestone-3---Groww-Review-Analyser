from collections.abc import Callable

import pytest

from pulse.evals.oracle import MUTATIONS, PulseInputs, summary_19_words, valid_inputs
from pulse.report.validate import ValidationIssue, route


def test_valid_pulse_passes() -> None:
    report = valid_inputs().check()
    assert report.passed and report.issues == []


@pytest.mark.parametrize("case", MUTATIONS, ids=lambda case: case[0].__name__)
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
