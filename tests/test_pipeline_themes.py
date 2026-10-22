import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from factories import make_review
from fakes import FakeLLM
from pydantic import BaseModel

from pulse.config import Settings
from pulse.llm.discover import DiscoveredTheme, DiscoveryOutput
from pulse.llm.schema import DailyBudgetExhausted, LLMRequest
from pulse.models import RunStatus, Taxonomy, Theme
from pulse.pipeline import RunOptions, RunOutcome, run_pipeline
from pulse.store import Store

TODAY = date(2026, 10, 19)  # window 2026-W42: 27 Jul – 18 Oct 2026
LATEST = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
OLD_THEMES = ("support", "charges", "bugs")
NEW_THEMES = ("orders", "charts", "kyc")


def seed_reviews(store: Store, count: int, **fields: object) -> None:
    prefix = str(fields.pop("prefix", "r"))
    store.upsert_reviews(
        [
            make_review(
                review_id=f"{prefix}{index:04d}",
                posted_at=LATEST - timedelta(days=7 * (index % 10)),
                **fields,
            )
            for index in range(count)
        ]
    )


def seed_taxonomy(store: Store, slugs: tuple[str, ...] = OLD_THEMES) -> None:
    themes = [
        Theme(id=slug, name=slug.title(), definition=slug, include_hints=[], exclude_hints=[])
        for slug in slugs
    ]
    store.save_taxonomy(
        Taxonomy(version=1, themes=themes, created_from_run="old", created_at=LATEST, sample_review_ids=[])
    )


def discovered(slugs: tuple[str, ...] = NEW_THEMES) -> DiscoveryOutput:
    return DiscoveryOutput(
        themes=[
            DiscoveredTheme(
                id=slug,
                name=slug.title(),
                definition=slug,
                include_hints=[],
                exclude_hints=[],
                example_keys=[],
            )
            for slug in slugs
        ]
    )


def keys_in(req: LLMRequest) -> list[int]:
    payload = req.user[req.user.index("<reviews>") + len("<reviews>") : req.user.index("</reviews>")]
    return [item["k"] for item in json.loads(payload)]


def labeller(themes_for: Callable[[str], tuple[str, ...]], sentiment: str = "negative") -> Callable:
    """Labels keys round-robin over the themes chosen for the request's taxonomy (from the system prompt)."""

    def handle(req: LLMRequest) -> BaseModel:
        themes = themes_for(req.system)
        items = [
            {"k": key, "theme_id": themes[key % len(themes)], "sentiment": sentiment} for key in keys_in(req)
        ]
        return req.schema.model_validate({"items": items})

    return handle


def current_themes(system: str) -> tuple[str, ...]:
    return NEW_THEMES if '"orders"' in system else OLD_THEMES


def run(settings: Settings, store: Store, llm: FakeLLM, **opts: object) -> RunOutcome:
    fields: dict[str, object] = {
        "today": TODAY,
        "until_stage": "aggregate",
        "started_at": datetime(2026, 10, 19, 5, tzinfo=UTC),
    }
    return run_pipeline(settings, RunOptions(**(fields | opts)), store=store, sources=[], llm=llm)


def classify_keys(llm: FakeLLM) -> int:
    return sum(len(keys_in(call)) for call in llm.calls if call.stage == "classify")


def test_until_aggregate_writes_taxonomy_and_aggregates(tmp_settings: Settings, tmp_store: Store) -> None:
    seed_reviews(tmp_store, 150)
    llm = FakeLLM({"discover": [discovered()], "classify": labeller(current_themes)})
    out = run(tmp_settings, tmp_store, llm)
    assert out.status == RunStatus.AGGREGATED
    taxonomy = json.loads((out.run_dir / "taxonomy.json").read_text())
    assert [theme["id"] for theme in taxonomy["themes"]] == list(NEW_THEMES)
    aggregates = json.loads((out.run_dir / "aggregates.json").read_text())
    assert aggregates["total"] == 150 and aggregates["window_total"] == 150
    report = (out.run_dir / "report.md").read_text()
    for heading in ("## Taxonomy", "## Classification", "## Top themes", "## LLM usage"):
        assert heading in report
    assert "From 150 eligible reviews" in report and "$0.00" in report


def test_too_few_reviews_stops_before_discovery(tmp_settings: Settings, tmp_store: Store) -> None:  # ING-6
    seed_reviews(tmp_store, 40)
    llm = FakeLLM({"discover": [discovered()], "classify": labeller(current_themes)})
    out = run(tmp_settings, tmp_store, llm)
    assert out.status == RunStatus.INSUFFICIENT_DATA and llm.calls == []
    assert "40 eligible reviews" in (out.run_dir / "report.md").read_text()


def test_insufficient_data_stops_run(tmp_settings: Settings, tmp_store: Store) -> None:  # AGG-3
    seed_reviews(tmp_store, 150)
    llm = FakeLLM({"discover": [discovered()], "classify": labeller(lambda system: ("orders", "charts"))})
    out = run(tmp_settings, tmp_store, llm)
    assert out.status == RunStatus.INSUFFICIENT_DATA
    assert "only 2 themes have reviews" in (out.run_dir / "report.md").read_text()


def test_high_unthemed_share_triggers_one_rediscovery(tmp_settings: Settings, tmp_store: Store) -> None:
    seed_reviews(tmp_store, 150)
    seed_taxonomy(tmp_store)
    old_unthemed = labeller(
        lambda system: NEW_THEMES if '"orders"' in system else ("unthemed", "unthemed", "support")
    )
    llm = FakeLLM({"discover": [discovered()], "classify": old_unthemed})
    out = run(tmp_settings, tmp_store, llm)
    assert out.status == RunStatus.AGGREGATED
    assert tmp_store.latest_taxonomy().version == 2
    assert tmp_store.latest_run("2026-W42")["summary"]["taxonomy_version"] == 2
    report = (out.run_dir / "report.md").read_text()
    assert "## Theme mapping" in report and "unthemed_high_after_rediscovery" not in report


def test_second_high_unthemed_share_warns_not_loops(
    tmp_settings: Settings, tmp_store: Store
) -> None:  # LLM-8
    seed_reviews(tmp_store, 150)
    seed_taxonomy(tmp_store)
    mostly_unthemed = labeller(lambda system: ("unthemed", "unthemed", "unthemed", *current_themes(system)))
    llm = FakeLLM({"discover": [discovered()], "classify": mostly_unthemed})
    out = run(tmp_settings, tmp_store, llm)
    assert tmp_store.latest_taxonomy().version == 2
    assert "unthemed_high_after_rediscovery" in (out.run_dir / "report.md").read_text()


def test_rediscovery_uses_actionable_unthemed_share(
    tmp_settings: Settings, tmp_store: Store
) -> None:  # LLM-16
    seed_reviews(tmp_store, 100, prefix="praise", rating=5)
    seed_reviews(tmp_store, 200, prefix="issue", rating=1)
    seed_taxonomy(tmp_store)

    def handle(req: LLMRequest) -> BaseModel:
        payload = req.user[req.user.index("<reviews>") + len("<reviews>") : req.user.index("</reviews>")]
        items = [
            {
                "k": item["k"],
                "theme_id": "unthemed" if item["rating"] == 5 else OLD_THEMES[item["k"] % 3],
                "sentiment": "positive" if item["rating"] == 5 else "negative",
            }
            for item in json.loads(payload)
        ]
        return req.schema.model_validate({"items": items})

    llm = FakeLLM({"discover": [discovered()], "classify": handle})
    out = run(tmp_settings, tmp_store, llm)
    assert out.status == RunStatus.AGGREGATED and tmp_store.latest_taxonomy().version == 1
    assert not any(call.stage == "discover" for call in llm.calls)


def test_budget_exhaustion_fails_run_with_resume_hint(
    tmp_settings: Settings, tmp_store: Store
) -> None:  # LLM-13
    seed_reviews(tmp_store, 150)
    seed_taxonomy(tmp_store)
    tight = FakeLLM({"classify": labeller(current_themes)}, tokens_left=2000, tokens_per_call=1000)
    out = run(tmp_settings, tmp_store, tight)
    assert out.status == RunStatus.FAILED
    report = (out.run_dir / "report.md").read_text()
    assert "daily_token_budget: classified 100 of 150" in report
    assert "pulse run --week 2026-W42 --from classify --until aggregate" in report
    later = FakeLLM({"classify": labeller(current_themes)})
    resumed = run(
        tmp_settings,
        tmp_store,
        later,
        from_stage="classify",
        started_at=datetime(2026, 10, 20, 1, tzinfo=UTC),
    )
    assert resumed.status == RunStatus.AGGREGATED and classify_keys(later) == 50
    assert resumed.run_id == out.run_id


def test_served_models_in_report(tmp_settings: Settings, tmp_store: Store) -> None:  # LLM-11
    seed_reviews(tmp_store, 120)
    seed_taxonomy(tmp_store)
    llm = FakeLLM({"classify": labeller(current_themes)}, served_model="openai/gpt-oss-120b")
    out = run(tmp_settings, tmp_store, llm)
    report = (out.run_dir / "report.md").read_text()
    assert "openai/gpt-oss-120b" in report
    assert tmp_store.latest_run("2026-W42")["summary"]["served_models"] == {"openai/gpt-oss-120b": 3}


def test_themes_use_only_eligible_reviews(tmp_settings: Settings, tmp_store: Store) -> None:  # ING-24
    seed_reviews(tmp_store, 150)
    seed_reviews(tmp_store, 300, prefix="short", excluded_reason="too_short", rating=5)
    llm = FakeLLM({"discover": [discovered()], "classify": labeller(current_themes)})
    out = run(tmp_settings, tmp_store, llm)
    assert classify_keys(llm) == 150
    aggregates = json.loads((out.run_dir / "aggregates.json").read_text())
    assert (aggregates["window_total"], aggregates["total"], aggregates["excluded"]) == (
        450,
        150,
        {"too_short": 300},
    )
    assert "From 150 eligible reviews" in (out.run_dir / "report.md").read_text()


def test_resume_keeps_earlier_report_sections(tmp_settings: Settings, tmp_store: Store) -> None:  # OPS-11
    seed_reviews(tmp_store, 150)
    seed_taxonomy(tmp_store)
    first = run(tmp_settings, tmp_store, FakeLLM({"classify": labeller(current_themes)}))
    assert "## Import" in (first.run_dir / "report.md").read_text()
    resumed = run(
        tmp_settings, tmp_store, FakeLLM({"classify": labeller(current_themes)}), from_stage="classify"
    )
    report = (resumed.run_dir / "report.md").read_text()
    assert "## Import" in report and "## Window" in report and "## Top themes" in report


def mostly_unthemed() -> Callable:
    return labeller(lambda system: ("unthemed", "unthemed", "unthemed", *current_themes(system)))


def out_of_budget_after(calls: int, handle: Callable, when: Callable[[LLMRequest], bool]) -> Callable:
    """Runs out of daily budget once `calls` matching requests have been answered."""
    answered: list[LLMRequest] = []

    def wrapped(req: LLMRequest) -> BaseModel:
        if when(req):
            if len(answered) >= calls:
                raise DailyBudgetExhausted(200_000, 200_000)
            answered.append(req)
        return handle(req)

    return wrapped


def resume(settings: Settings, store: Store, llm: FakeLLM, stage: str = "classify") -> RunOutcome:
    return run(settings, store, llm, from_stage=stage, started_at=datetime(2026, 10, 20, 1, tzinfo=UTC))


def test_resume_does_not_rediscover_taxonomy_created_in_run(
    tmp_settings: Settings, tmp_store: Store
) -> None:  # LLM-8, review C1
    seed_reviews(tmp_store, 150)
    tight = out_of_budget_after(2, mostly_unthemed(), lambda req: True)
    first = run(tmp_settings, tmp_store, FakeLLM({"discover": [discovered()], "classify": tight}))
    assert first.status == RunStatus.FAILED and tmp_store.latest_taxonomy().version == 1
    later = FakeLLM({"discover": [discovered(("x1", "x2", "x3"))], "classify": mostly_unthemed()})
    resumed = resume(tmp_settings, tmp_store, later)
    assert resumed.status == RunStatus.AGGREGATED and tmp_store.latest_taxonomy().version == 1
    assert not any(call.stage == "discover" for call in later.calls)


def test_resume_after_rediscovery_does_not_rediscover_again(
    tmp_settings: Settings, tmp_store: Store
) -> None:  # LLM-8, review C1
    seed_reviews(tmp_store, 150)
    seed_taxonomy(tmp_store)
    new_taxonomy = out_of_budget_after(1, mostly_unthemed(), lambda req: '"orders"' in req.system)
    first = run(tmp_settings, tmp_store, FakeLLM({"discover": [discovered()], "classify": new_taxonomy}))
    assert first.status == RunStatus.FAILED and tmp_store.latest_taxonomy().version == 2
    later = FakeLLM({"discover": [discovered(("x1", "x2", "x3"))], "classify": mostly_unthemed()})
    resumed = resume(tmp_settings, tmp_store, later)
    assert resumed.status == RunStatus.AGGREGATED and tmp_store.latest_taxonomy().version == 2
    assert not any(call.stage == "discover" for call in later.calls)
    assert "unthemed_high_after_rediscovery" in (resumed.run_dir / "report.md").read_text()


def test_resume_drops_warnings_from_rerun_stages(tmp_settings: Settings, tmp_store: Store) -> None:  # OPS-11
    seed_reviews(tmp_store, 150)
    seed_taxonomy(tmp_store, NEW_THEMES)
    broad = labeller(lambda system: ("orders", "orders", "charts", "kyc"))
    first = run(tmp_settings, tmp_store, FakeLLM({"classify": broad}))
    assert first.status == RunStatus.AGGREGATED
    resumed = resume(tmp_settings, tmp_store, FakeLLM({"classify": broad}), stage="aggregate")
    assert (resumed.run_dir / "report.md").read_text().count("broad_theme:orders") == 1


def test_resume_drops_budget_warning_from_older_report(
    tmp_settings: Settings, tmp_store: Store
) -> None:  # OPS-11, LLM-13
    seed_reviews(tmp_store, 150)
    seed_taxonomy(tmp_store)
    tight = FakeLLM({"classify": labeller(current_themes)}, tokens_left=2000, tokens_per_call=1000)
    first = run(tmp_settings, tmp_store, tight)
    state_path = first.run_dir / "report.json"
    state = json.loads(state_path.read_text())
    state.pop("warning_stages", None)  # a report saved before warnings were tagged by stage
    state_path.write_text(json.dumps(state))
    resumed = resume(tmp_settings, tmp_store, FakeLLM({"classify": labeller(current_themes)}))
    assert resumed.status == RunStatus.AGGREGATED
    assert "daily_token_budget" not in (resumed.run_dir / "report.md").read_text()
