"""The free, LLM-free suite run on every commit: E-QUO-1, E-PII-1, E-PII-3, E-TAX-4 (evaluation §4, Task 4.5).

The oracle and null baselines run first; if either misbehaves the suite aborts and writes nothing (§5 rule 7).
Results carry IDs, file names and counts only, never review text or matched personal data.
"""

import csv
import json
import re
import shutil
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

from pulse.config import Settings
from pulse.evals.oracle import MUTATIONS, PulseInputs, valid_inputs
from pulse.evals.runner import RESULTS_ROOT, EvalRun
from pulse.evals.suites.classification import EvalAborted
from pulse.ingest.file_import import FileImportSource
from pulse.llm.discover import THEME_ID_RE
from pulse.llm.quotes import verify_quote
from pulse.llm.schema import UsageLedger
from pulse.models import UNTHEMED
from pulse.pipeline import RunOptions, run_pipeline
from pulse.privacy.scrub import PLACEHOLDER_RE, find_pii
from pulse.report.render import render_markdown
from pulse.report.validate import validate
from pulse.store import Store

REDTEAM_PATH = Path("evals/redteam/pii_reviews.csv")
REDTEAM_TODAY = date(2026, 10, 19)  # the red-team rows are dated in 2026-W41
NO_MODEL = "none"
# A review ID is a 16-character hex hash whose digit runs can look like Aadhaar or phone numbers. A token is
# skipped if it has a hex letter or is a stored review ID; any other all-digit value is still checked.
HEX_TOKEN = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{16}(?![0-9A-Za-z])")


def run_deterministic_suite(
    settings: Settings, *, out_root: Path = RESULTS_ROOT, redteam: Path = REDTEAM_PATH
) -> Path:
    run = EvalRun("deterministic", out_root)
    _baselines(run)
    store = Store(Path(settings.paths.db))
    _quote_fidelity(run, Path(settings.paths.runs), store, settings.pulse.quote_max_words)
    _artifact_scan(run, [Path(settings.paths.runs), Path(out_root)], store.review_ids())
    _redteam_recall(run, settings, Path(redteam))
    _taxonomy_shape(run, store, settings)
    return run.finish(UsageLedger(), settings.llm.pricing)


def _baselines(run: EvalRun) -> None:
    """Golden pulse passes, every gate mutation fails; real quote spans verify, altered ones don't."""
    mutated = []
    for mutate, _ in MUTATIONS:
        inputs = valid_inputs()
        mutate(inputs)
        mutated.append(_passes(inputs))
    quotes = valid_inputs()
    real = [verify_quote(quote.text, quotes.reviews[quote.review_id].text) for quote in quotes.quotes]
    altered = [
        verify_quote("xyzzy " + quote.text.split(" ", 1)[1], quotes.reviews[quote.review_id].text)
        for quote in quotes.quotes
    ]
    scores = {
        "ORACLE-validators": float(_passes(valid_inputs())),
        "NULL-validators": sum(mutated) / len(mutated),
        "ORACLE-quotes": sum(real) / len(real),
        "NULL-quotes": sum(altered) / len(altered),
    }
    for metric_id, value in scores.items():
        oracle = metric_id.startswith("ORACLE")
        run.metric(metric_id, value, target=1.0 if oracle else 0.0, gate="blocking", higher_is_better=oracle)
    broken = [metric_id for metric_id, metric in run.metrics.items() if metric["pass"] is False]
    if broken:
        raise EvalAborted(
            f"baseline misbehaved: {', '.join(broken)}; the validators or verify_quote are broken"
        )


def _passes(inputs: PulseInputs) -> bool:
    ctx = inputs.context()
    top_ids = [stats.theme_id for stats in inputs.top]
    markdown = render_markdown(ctx)
    return validate(
        ctx, markdown, top_ids, inputs.taxonomy, inputs.reviews, inputs.labels, inputs.limits
    ).passed


def _quote_fidelity(run: EvalRun, runs_dir: Path, store: Store, max_words: int) -> None:
    """E-QUO-1: every quote in every runs/*/quotes.json is a verbatim span of its stored review."""
    checked = verified = 0
    for path in sorted(runs_dir.glob("*/quotes.json")):
        quotes = json.loads(path.read_text(encoding="utf-8")).get("quotes", [])
        reviews = {
            review.review_id: review for review in store.get_reviews([quote["review_id"] for quote in quotes])
        }
        for quote in quotes:
            review = reviews.get(quote["review_id"])
            passed = review is not None and verify_quote(quote["text"], review.text, max_words)
            checked, verified = checked + 1, verified + passed
            record = {
                "check": "quote",
                "run": path.parent.name,
                "review_id": quote["review_id"],
                "verified": passed,
            }
            run.case(
                f"{path.parent.name}:{quote['review_id']}",
                record,
                served_model=NO_MODEL,
                primary_model=NO_MODEL,
            )
    run.metric("E-QUO-1", verified / checked if checked else None, target=1.0, gate="blocking")


def _artifact_scan(run: EvalRun, roots: list[Path], known_ids: set[str]) -> None:
    """E-PII-1: personal data, or a redaction placeholder inside a quote, in any artifact or log."""
    hits = 0
    files = [path for root in roots if root.is_dir() for path in sorted(root.rglob("*")) if path.is_file()]
    for path in files:
        kinds = _pii_kinds(path, known_ids)
        if not kinds:
            continue
        hits += len(kinds)
        record = {"check": "pii", "file": str(path), "kinds": sorted(set(kinds)), "count": len(kinds)}
        run.case(f"pii:{path.name}", record, served_model=NO_MODEL, primary_model=NO_MODEL)
    run.metric("E-PII-1", hits, target=0, gate="blocking", higher_is_better=False)


def _pii_kinds(path: Path, known_ids: set[str]) -> list[str]:
    text = HEX_TOKEN.sub(
        lambda token: _mask_review_id(token.group(), known_ids),
        path.read_text(encoding="utf-8", errors="ignore"),
    )
    kinds = [match.kind for match in find_pii(text)]
    if path.name == "quotes.json":
        quotes = json.loads(text).get("quotes", [])
        kinds += ["placeholder" for quote in quotes if PLACEHOLDER_RE.search(quote["text"])]
    return kinds


def _mask_review_id(token: str, known_ids: set[str]) -> str:
    return "<review-id>" if token in known_ids or not token.isdigit() else token


def _redteam_recall(run: EvalRun, settings: Settings, redteam: Path) -> None:
    """E-PII-3: seeded fake PII never reaches a stored Review; rupee amounts survive unchanged."""
    with redteam.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        paths = settings.paths.model_copy(
            update={"db": root / "pulse.db", "runs": root / "runs", "raw": root / "raw"}
        )
        local = settings.model_copy(update={"paths": paths}, deep=True)
        Path(paths.raw).mkdir()
        shutil.copy(redteam, Path(paths.raw) / "generic__pii_reviews.csv")
        store = Store(Path(paths.db))
        source = FileImportSource(Path(paths.raw), Path(paths.mappings))
        run_pipeline(
            local, RunOptions(today=REDTEAM_TODAY, until_stage="ingest"), store=store, sources=[source]
        )
        stored = store.reviews_between(datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC))
    blob = "".join(review.model_dump_json() for review in stored)
    seeded = [value for row in rows for value in row["seeded_pii"].split("|") if value]
    negatives = [row for row in rows if row["negative"] == "true"]
    run.metric(
        "E-PII-3", sum(value not in blob for value in seeded) / len(seeded), target=1.0, gate="blocking"
    )
    false_positives = sum(row["text"] not in blob for row in negatives)
    run.metric("E-PII-3-false-positives", false_positives, target=0, gate="blocking", higher_is_better=False)


def _taxonomy_shape(run: EvalRun, store: Store, settings: Settings) -> None:
    """E-TAX-4: 3–5 themes, names of at most 4 words, valid IDs, `unthemed` not among them."""
    taxonomy = store.latest_taxonomy()
    if taxonomy is None:
        run.metric("E-TAX-4", None, target=1.0, gate="blocking")
        return
    rules = settings.themes
    ok = rules.min <= len(taxonomy.themes) <= rules.max and all(
        len(theme.name.split()) <= rules.name_max_words
        and THEME_ID_RE.match(theme.id)
        and theme.id != UNTHEMED
        for theme in taxonomy.themes
    )
    run.metric("E-TAX-4", float(ok), target=1.0, gate="blocking")
