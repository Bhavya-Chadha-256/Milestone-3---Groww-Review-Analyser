import csv
import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest
from factories import make_review
from fakes import FakeLLM
from pydantic import BaseModel

from pulse.config import Settings
from pulse.evals.oracle import valid_inputs
from pulse.evals.suites.classification import EvalAborted
from pulse.evals.suites.deterministic import run_deterministic_suite
from pulse.evals.suites.pulses import run_pulses_suite
from pulse.llm.discover import DiscoveredTheme, DiscoveryOutput
from pulse.llm.schema import LLMRequest
from pulse.report.validate import ValidationReport
from pulse.store import Store

REPO = Path(__file__).resolve().parents[1]
WEEKS = REPO / "tests" / "fixtures" / "weeks"
REDTEAM = REPO / "evals" / "redteam" / "pii_reviews.csv"
DATASETS = ("normal", "thin", "burst")


def generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("weeks_generate", WEEKS / "generate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rows(dataset: str) -> list[dict[str, str]]:
    with (WEEKS / dataset / "generic__data.csv").open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def metrics(out: Path) -> dict[str, dict]:
    return json.loads((out / "summary.json").read_text())["metrics"]


def test_generator_is_deterministic(tmp_path: Path) -> None:
    generate = generator().generate
    generate(tmp_path / "first")
    generate(tmp_path / "second")
    for dataset in DATASETS:
        name = Path(dataset) / "generic__data.csv"
        fresh = (tmp_path / "first" / name).read_bytes()
        assert fresh == (tmp_path / "second" / name).read_bytes() == (WEEKS / name).read_bytes()


def test_thin_dataset_has_two_populated_themes() -> None:  # AGG-3
    theme_of = generator().theme_of
    thin = rows("thin")
    assert len(thin) == 120 and {theme_of(row["text"]) for row in thin} == {"withdrawals", "charges"}
    normal_themes = {theme_of(row["text"]) for row in rows("normal")}
    assert normal_themes == {"withdrawals", "kyc", "app_performance", "support", "charges", None}


def test_burst_dataset_adds_identical_latest_week_reviews() -> None:  # AGG-8
    burst = rows("burst")
    texts = [row["text"] for row in burst if row["id"].startswith("b")]
    assert len(texts) == 60 and len(set(texts)) == 1
    assert all(
        row["date"] >= "2026-10-12" and row["rating"] == "1" for row in burst if row["id"].startswith("b")
    )


def deterministic(settings: Settings, tmp_path: Path) -> Path:
    return run_deterministic_suite(settings, out_root=tmp_path / "results", redteam=REDTEAM)


def test_deterministic_suite_flags_altered_quote_and_seeded_pii(
    tmp_settings: Settings, tmp_store: Store, tmp_path: Path
) -> None:  # E-QUO-1, E-PII-1
    tmp_store.upsert_reviews(
        [make_review(review_id="r1", text="Withdrawal stuck for three days and nobody replies")]
    )
    tmp_store.save_taxonomy(valid_inputs().taxonomy)
    run_dir = Path(tmp_settings.paths.runs) / "2026-W42-20261019T050000"
    run_dir.mkdir(parents=True)
    quote = {
        "review_id": "r1",
        "theme_id": "withdrawals",
        "rating": 1,
        "source": "play_store",
        "month": "Oct 2026",
    }
    quotes = [
        quote | {"text": "Withdrawal stuck for three days"},
        quote | {"text": "Withdrawal blocked for three days"},
    ]
    (run_dir / "quotes.json").write_text(json.dumps({"quotes": quotes}))
    (Path(tmp_settings.paths.runs) / "launchd.log").write_text("mail ravi.kumar@example.com about it\n")
    found = metrics(deterministic(tmp_settings, tmp_path))
    assert (found["E-QUO-1"]["value"], found["E-QUO-1"]["pass"]) == (0.5, False)
    assert found["E-PII-1"]["value"] >= 1 and found["E-PII-1"]["pass"] is False
    assert found["E-PII-3"]["value"] == 1.0 and found["E-PII-3"]["pass"] is True
    assert found["E-TAX-4"]["value"] == 1.0


def test_deterministic_oracle_and_null_baselines(tmp_settings: Settings, tmp_path: Path) -> None:
    found = metrics(deterministic(tmp_settings, tmp_path))
    assert found["ORACLE-validators"]["value"] == 1.0 and found["NULL-validators"]["value"] == 0.0
    assert found["ORACLE-quotes"]["value"] == 1.0 and found["NULL-quotes"]["value"] == 0.0
    assert found["E-QUO-1"]["value"] is None and found["E-PII-1"]["value"] == 0


def test_deterministic_suite_aborts_when_a_baseline_misbehaves(
    tmp_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # evaluation §5 rule 7
    monkeypatch.setattr(
        "pulse.evals.suites.deterministic.validate", lambda *args: ValidationReport(passed=True)
    )
    with pytest.raises(EvalAborted, match="NULL-validators"):
        deterministic(tmp_settings, tmp_path)
    assert not (tmp_path / "results").exists()


def discovered() -> DiscoveryOutput:
    slugs = ("withdrawals", "kyc", "app_performance", "support", "charges")
    return DiscoveryOutput(
        themes=[
            DiscoveredTheme(
                id=slug,
                name=slug.replace("_", " ").title(),
                definition=f"Reviews about {slug}",
                include_hints=[],
                exclude_hints=[],
                example_keys=[],
            )
            for slug in slugs
        ]
    )


def items(req: LLMRequest) -> list[dict]:
    return json.loads(req.user[req.user.index("<reviews>") + len("<reviews>") : req.user.index("</reviews>")])


def fake_pipeline_llm() -> FakeLLM:
    theme_of = generator().theme_of

    def classify(req: LLMRequest) -> BaseModel:
        labels = []
        for item in items(req):
            theme = theme_of(item["text"])
            sentiment = "negative" if theme else "positive"
            labels.append({"k": item["k"], "theme_id": theme or "unthemed", "sentiment": sentiment})
        return req.schema.model_validate({"items": labels})

    def quote(req: LLMRequest) -> BaseModel:
        first = items(req)[0]
        return req.schema.model_validate({"k": first["k"], "quote": " ".join(first["text"].split()[:6])})

    return FakeLLM({"discover": [discovered()], "classify": classify, "quotes": quote, "compose": composer()})


def composer() -> Callable:
    """A valid draft every time, except the second call (burst's first attempt): 19-word summaries."""
    calls = []

    def handle(req: LLMRequest) -> BaseModel:
        calls.append(req)
        block = req.user.split("Top themes, in order:\n", 1)[1].split("\n", 1)[0]
        ids = [theme["theme_id"] for theme in json.loads(block)]
        summary = " ".join(["slow"] * 19) if len(calls) == 2 else "Users report repeated trouble here."
        return req.schema.model_validate(
            {
                "themes": [{"theme_id": theme, "summary": summary} for theme in ids],
                "actions": [
                    {"theme_ids": [theme], "action": f"Fix the {theme} flow.", "rationale": "Users ask."}
                    for theme in ids
                ],
            }
        )

    return handle


def test_pulses_suite_records_first_attempt_pass(
    tmp_settings: Settings, tmp_path: Path
) -> None:  # E-PUL-1, E-PUL-2
    out = run_pulses_suite(
        tmp_settings, fake_pipeline_llm(), datasets_dir=WEEKS, out_root=tmp_path / "results"
    )
    found = metrics(out)
    assert found["E-PUL-1"]["value"] == 1.0 and found["E-PUL-2"]["value"] == 0.5
    assert found["E-QUO-1"]["value"] == 1.0 and found["E-ACT-5"]["value"] == 1.0
    assert found["burst-duplicate-share"]["value"] > 0.10
    cases = [json.loads(line) for line in (out / "cases.jsonl").read_text().splitlines()]
    assert {case["case_id"]: case["status"] for case in cases} == {
        "normal": "VALIDATED",
        "burst": "VALIDATED",
        "thin": "INSUFFICIENT_DATA",
    }


def test_artifact_scan_ignores_review_ids(
    tmp_settings: Settings, tmp_store: Store, tmp_path: Path
) -> None:  # E-PII-1
    run_dir = Path(tmp_settings.paths.runs) / "2026-W42-20261019T050000"
    run_dir.mkdir(parents=True)
    tmp_store.upsert_reviews([make_review(review_id="1234123412341234")])  # an all-digit hash, but a known ID
    ids = ["a987654321098765", "9876543210123b45", "c1234567890123de", "1234123412341234"]
    (run_dir / "taxonomy.json").write_text(json.dumps({"sample_review_ids": ids}))
    assert metrics(deterministic(tmp_settings, tmp_path))["E-PII-1"]["value"] == 0
    (run_dir / "notes.txt").write_text("account 1234567890123456 and phone 9876543210\n")
    assert metrics(deterministic(tmp_settings, tmp_path / "again"))["E-PII-1"]["value"] >= 2


def test_artifact_scan_ignores_decimal_fractions(tmp_settings: Settings, tmp_path: Path) -> None:  # E-PII-1
    run_dir = Path(tmp_settings.paths.runs) / "2026-W42-20261019T050000"
    run_dir.mkdir(parents=True)
    shares = {
        "window_mean_rating": 3.4074074074074074,
        "duplicate_share": 0.07407407407407407,
        "share": 0.3037037037037037,
    }
    (run_dir / "aggregates.json").write_text(json.dumps(shares))
    assert metrics(deterministic(tmp_settings, tmp_path))["E-PII-1"]["value"] == 0
