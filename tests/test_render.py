from pathlib import Path

import pytest

from pulse.evals.oracle import aggregates, valid_inputs
from pulse.report.render import build_context, render_html, render_markdown, render_text
from pulse.report.words import count_words, escape_md
from pulse.timeutil import window_for_week

GOLDEN = Path(__file__).parent / "fixtures" / "pulse_golden.md"


def test_count_words_rule() -> None:
    assert count_words("# Top themes\n1. **Withdrawals** — 22% (↑4 pts).") == 6
    assert count_words('> "Paisa kat gaya bina bataye" — ★1') == 6
    assert count_words("डेटा नहीं दिख रहा") == 4


def test_escape_md_escapes_markdown_syntax() -> None:
    assert escape_md("a*b_c#d>e[f]g`h\\i") == "a\\*b\\_c\\#d\\>e\\[f\\]g\\`h\\\\i"


def test_golden_render_matches_and_fits_budget() -> None:  # REN-11
    markdown = valid_inputs().markdown()
    assert markdown == GOLDEN.read_text(encoding="utf-8")
    assert count_words(markdown) <= 250
    assert "· Play Store ·" in markdown and "App Store" not in markdown


@pytest.mark.parametrize(
    "case",
    [
        ((4, True), " (↑4 pts this week)"),
        ((-3, True), " (↓3 pts this week)"),
        ((1, False), " (steady this week)"),
        ((None, False), ""),
    ],
)
def test_trend_clause_variants(case: tuple[tuple[int | None, bool], str]) -> None:
    (pts, significant), expected = case
    inputs = valid_inputs()
    inputs.top[0].trend_pts, inputs.top[0].trend_significant = pts, significant
    line = next(line for line in inputs.markdown().splitlines() if line.startswith("1. "))
    assert line.startswith(f"1. **Withdrawals** — 30% of reviews{expected}. Payouts")


def test_meta_line_shows_both_years_across_year_boundary() -> None:  # AGG-6
    inputs = valid_inputs()
    inputs.agg = aggregates(window_for_week("2026-W53", 12, "Asia/Kolkata"))
    assert (
        "12 Oct 2026 – 3 Jan 2027"
        in build_context(inputs.agg, inputs.top, inputs.draft, inputs.quotes, inputs.taxonomy).meta_line
    )


def test_meta_line_names_both_sources() -> None:
    inputs = valid_inputs()
    inputs.agg = aggregates(sources={"play_store": 1200, "app_store": 40})
    assert "· Play Store + App Store ·" in inputs.markdown()


def quote_review(text: str) -> object:
    inputs = valid_inputs()
    inputs.reviews["withdrawals0"] = inputs.reviews["withdrawals0"].model_copy(
        update={"text": f"Honestly {text} ever"}
    )
    inputs.quotes[0] = inputs.quotes[0].model_copy(update={"text": text})
    return inputs


def test_markdown_special_chars_in_quote_are_escaped() -> None:  # REN-6
    inputs = quote_review("I *love* #1 app")
    markdown = render_markdown(inputs.context())
    assert '"I \\*love\\* \\#1 app"' in markdown
    assert inputs.quotes[0].text == "I *love* #1 app"
    assert inputs.check(markdown).passed


def test_render_text_has_no_markdown_and_html_escapes_review_text() -> None:  # REN-7
    inputs = quote_review("the <b>app</b> & charts")
    text = render_text(inputs.context())
    assert "**" not in text and "\n#" not in text and not text.startswith("#") and "\n>" not in text
    assert '"the <b>app</b> & charts"' in text
    html = render_html(inputs.context())
    assert "&lt;b&gt;app&lt;/b&gt; &amp; charts" in html and "<b>app</b>" not in html
    assert "<h1>" in html and "<ol>" in html
