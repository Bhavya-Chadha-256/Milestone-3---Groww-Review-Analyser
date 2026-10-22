import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pytest
from factories import make_review
from fakes import FakeLLM
from pydantic import BaseModel

from pulse.config import Settings
from pulse.llm.schema import DailyBudgetExhausted, LLMRefusal, LLMRequest, LLMTruncated
from pulse.models import RunStatus, Taxonomy, Theme
from pulse.pipeline import MissingArtifact, RunOptions, RunOutcome, run_pipeline
from pulse.report.words import count_words
from pulse.store import Store

TODAY = date(2026, 10, 19)
LATEST = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
THEMES = ("support", "charges", "bugs")
ARTIFACTS = ("quotes.json", "pulse_draft.json", "pulse.md", "validation.json")


def seed(store: Store, **fields: object) -> None:
    store.upsert_reviews(
        [
            make_review(
                review_id=f"r{index:04d}",
                posted_at=LATEST - timedelta(days=7 * (index % 10)),
                text=f"Review number {index} says the app keeps failing when I open it",
                word_count=12,
                **fields,
            )
            for index in range(150)
        ]
    )
    themes = [
        Theme(id=slug, name=slug.title(), definition=slug, include_hints=[], exclude_hints=[])
        for slug in THEMES
    ]
    store.save_taxonomy(
        Taxonomy(version=1, themes=themes, created_from_run="old", created_at=LATEST, sample_review_ids=[])
    )


def payload(req: LLMRequest) -> list[dict]:
    return json.loads(req.user[req.user.index("<reviews>") + len("<reviews>") : req.user.index("</reviews>")])


def classify(req: LLMRequest) -> BaseModel:
    items = [
        {"k": item["k"], "theme_id": THEMES[item["k"] % 3], "sentiment": "negative"} for item in payload(req)
    ]
    return req.schema.model_validate({"items": items})


def pick_quote(req: LLMRequest) -> BaseModel:
    first = payload(req)[0]
    return req.schema.model_validate({"k": first["k"], "quote": " ".join(first["text"].split()[:6])})


def top_ids(req: LLMRequest) -> list[str]:
    block = req.user.split("Top themes, in order:\n", 1)[1].split("\n", 1)[0]
    return [theme["theme_id"] for theme in json.loads(block)]


def drafter(
    summary: Callable[[str], str] = lambda theme: f"Users report trouble with {theme} this week.",
) -> Callable:
    def handle(req: LLMRequest) -> BaseModel:
        ids = top_ids(req)
        return req.schema.model_validate(
            {
                "themes": [{"theme_id": theme, "summary": summary(theme)} for theme in ids],
                "actions": [
                    {
                        "theme_ids": [theme],
                        "action": f"Improve the {theme} flow.",
                        "rationale": "Users ask for it.",
                    }
                    for theme in ids
                ],
            }
        )

    return handle


def run(settings: Settings, store: Store, llm: FakeLLM, **opts: object) -> RunOutcome:
    fields: dict[str, object] = {
        "today": TODAY,
        "until_stage": "validate",
        "started_at": datetime(2026, 10, 19, 5, tzinfo=UTC),
    }
    return run_pipeline(settings, RunOptions(**(fields | opts)), store=store, sources=[], llm=llm)


def calls(llm: FakeLLM, stage: str) -> int:
    return sum(call.stage == stage for call in llm.calls)


def test_dry_run_produces_validated_pulse(tmp_settings: Settings, tmp_store: Store) -> None:
    seed(tmp_store)
    llm = FakeLLM({"classify": classify, "quotes": pick_quote, "compose": drafter()})
    out = run(tmp_settings, tmp_store, llm)
    assert out.status == RunStatus.VALIDATED
    assert all((out.run_dir / name).is_file() for name in ARTIFACTS)
    markdown = (out.run_dir / "pulse.md").read_text()
    assert count_words(markdown) <= 250 and markdown.startswith("# Groww Review Pulse — Week of 12 Oct 2026")
    assert json.loads((out.run_dir / "validation.json").read_text())["passed"] is True
    summary = tmp_store.latest_run("2026-W42")["summary"]
    assert (summary["validation_retries"], summary["quote_fallbacks"]) == (0, 0)
    report = (out.run_dir / "report.md").read_text()
    assert "## Pulse" in report and "## Validation" in report and "## Quotes" in report


def test_validation_failure_keeps_artifacts_and_stops(
    tmp_settings: Settings, tmp_store: Store
) -> None:  # OPS-4
    seed(tmp_store)
    wordy = drafter(lambda theme: " ".join(["slow"] * 19))
    llm = FakeLLM({"classify": classify, "quotes": pick_quote, "compose": wordy})
    out = run(tmp_settings, tmp_store, llm)
    assert out.status == RunStatus.VALIDATION_FAILED and calls(llm, "compose") == 3
    validation = json.loads((out.run_dir / "validation.json").read_text())
    assert validation["passed"] is False and "V5" in {issue["code"] for issue in validation["issues"]}
    assert all((out.run_dir / name).is_file() for name in ARTIFACTS)
    assert "Fix these problems from your previous draft:\n- V5: summary for" in llm.calls[-1].user
    assert "slow slow" not in (out.run_dir / "report.md").read_text()  # only a validated pulse is shown


def test_quote_routed_failure_reruns_quotes(
    tmp_settings: Settings, tmp_store: Store
) -> None:  # V6 → route "quotes"
    seed(tmp_store)
    drafts = iter([drafter(lambda theme: "Write to ravi.kumar@example.com for help.")])

    def compose(req: LLMRequest) -> BaseModel:
        return next(drafts, drafter())(req)

    llm = FakeLLM({"classify": classify, "quotes": pick_quote, "compose": compose})
    out = run(tmp_settings, tmp_store, llm)
    assert out.status == RunStatus.VALIDATED
    assert (calls(llm, "quotes"), calls(llm, "compose")) == (6, 2)
    assert tmp_store.latest_run("2026-W42")["summary"]["validation_retries"] == 1


def test_quote_unavailable_fails_validation(tmp_settings: Settings, tmp_store: Store) -> None:  # QUO-3
    seed(tmp_store, is_duplicate_text=True)
    llm = FakeLLM({"classify": classify, "quotes": pick_quote, "compose": drafter()})
    out = run(tmp_settings, tmp_store, llm)
    assert out.status == RunStatus.VALIDATION_FAILED and calls(llm, "compose") == 0
    assert "no quotable review for theme" in (out.run_dir / "report.md").read_text()


def test_resume_from_compose_with_missing_quotes_errors_clearly(
    tmp_settings: Settings, tmp_store: Store
) -> None:  # OPS-1
    seed(tmp_store)
    first = run(
        tmp_settings, tmp_store, FakeLLM({"classify": classify, "quotes": pick_quote, "compose": drafter()})
    )
    (first.run_dir / "quotes.json").unlink()
    with pytest.raises(MissingArtifact, match="quotes.json"):
        run(tmp_settings, tmp_store, FakeLLM({"compose": drafter()}), from_stage="compose")


def test_resume_from_compose_reuses_saved_quotes(tmp_settings: Settings, tmp_store: Store) -> None:
    seed(tmp_store)
    first = run(
        tmp_settings, tmp_store, FakeLLM({"classify": classify, "quotes": pick_quote, "compose": drafter()})
    )
    saved = json.loads((first.run_dir / "quotes.json").read_text())["quotes"]
    later = FakeLLM({"compose": drafter()})
    resumed = run(tmp_settings, tmp_store, later, from_stage="compose")
    assert (
        resumed.status == RunStatus.VALIDATED and calls(later, "quotes") == 0 and calls(later, "compose") == 1
    )
    assert json.loads((resumed.run_dir / "quotes.json").read_text())["quotes"] == saved


def failing_first(error: Exception, times: int, then: Callable) -> Callable:
    """Raises `error` for the first `times` calls, then answers with `then`."""
    raised: list[Exception] = []

    def handle(req: LLMRequest) -> BaseModel:
        if len(raised) < times:
            raised.append(error)
            raise error
        return then(req)

    return handle


def test_daily_budget_in_compose_fails_with_resume_hint(
    tmp_settings: Settings, tmp_store: Store
) -> None:  # LLM-13
    seed(tmp_store)
    out_of_budget = failing_first(DailyBudgetExhausted(200_000, 200_000), 99, drafter())
    out = run(
        tmp_settings,
        tmp_store,
        FakeLLM({"classify": classify, "quotes": pick_quote, "compose": out_of_budget}),
    )
    assert out.status == RunStatus.FAILED
    report = (out.run_dir / "report.md").read_text()
    assert (
        "daily_token_budget" in report
        and "pulse run --week 2026-W42 --from compose --until validate" in report
    )


def test_compose_error_is_retried_as_a_failed_attempt(tmp_settings: Settings, tmp_store: Store) -> None:
    seed(tmp_store)
    truncated_once = failing_first(LLMTruncated("response hit max_completion_tokens"), 1, drafter())
    llm = FakeLLM({"classify": classify, "quotes": pick_quote, "compose": truncated_once})
    out = run(tmp_settings, tmp_store, llm)
    assert out.status == RunStatus.VALIDATED and calls(llm, "compose") == 2


def test_compose_that_never_answers_fails_validation(tmp_settings: Settings, tmp_store: Store) -> None:
    seed(tmp_store)
    refusing = failing_first(LLMRefusal("content_filter"), 99, drafter())
    llm = FakeLLM({"classify": classify, "quotes": pick_quote, "compose": refusing})
    out = run(tmp_settings, tmp_store, llm)
    assert out.status == RunStatus.VALIDATION_FAILED and calls(llm, "compose") == 3
    assert "compose failed after 3 attempts" in (out.run_dir / "report.md").read_text()
