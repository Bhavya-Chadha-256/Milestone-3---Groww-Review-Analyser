"""Eval run records: cases, errors, metrics and the files under evals/results/ (evaluation §5, §11)."""

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pulse.config import Pricing
from pulse.evals.metrics import paired_bootstrap_ci
from pulse.llm.schema import UsageLedger

RESULTS_ROOT = Path("evals/results")


@dataclass(frozen=True)
class CompareRow:
    metric_id: str
    a_value: float | None
    b_value: float | None
    verdict: str  # improved | regressed | within noise


class EvalRun:
    def __init__(self, suite: str, out_root: Path = RESULTS_ROOT, *, today: str | None = None) -> None:
        self.suite = suite
        self.out_dir = Path(out_root) / f"{today or datetime.now(UTC).date().isoformat()}-{suite}"
        self.cases: list[dict[str, Any]] = []
        self.errors: list[dict[str, str]] = []
        self.metrics: dict[str, dict[str, Any]] = {}

    def case(self, case_id: str, record: dict, *, served_model: str, primary_model: str) -> None:
        """Rows served by an unexpected model are kept but left out of the headline (§5 rule 3, LLM-11)."""
        self.cases.append({"case_id": case_id, **record, "unexpected_model": served_model != primary_model})

    def error(self, case_id: str, failure_class: str, detail: str) -> None:
        """Infrastructure failures are never scored as wrong answers (§5 rule 2)."""
        self.errors.append({"case_id": case_id, "failure_class": failure_class, "detail": detail})

    def headline_cases(self) -> list[dict[str, Any]]:
        return [case for case in self.cases if not case["unexpected_model"]]

    def metric(  # craftsman-ignore: WARN-PY001, PY003 (plan interface; hint on closing line)
        self,
        metric_id: str,
        value: float | None,
        *,
        target: float | None = None,
        gate: str,
        ci: tuple[float, float] | None = None,
        higher_is_better: bool = True,
    ) -> None:
        passed = None
        if value is not None and target is not None:
            passed = value >= target if higher_is_better else value <= target
        self.metrics[metric_id] = {
            "value": value,
            "ci95": list(ci) if ci else None,
            "target": target,
            "gate": gate,
            "pass": passed,
        }

    def finish(self, usage: UsageLedger, pricing: Pricing, judge_usage: UsageLedger | None = None) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        summary = {
            "suite": self.suite,
            "git_sha": _git_sha(),
            "metrics": self.metrics,
            "attempts": len(self.cases) + len(self.errors),
            "scored": len(self.cases),
            "errors": len(self.errors),
            "unexpected_model": sum(case["unexpected_model"] for case in self.cases),
            "usage": usage.by_stage(),
            "tokens_total": usage.total_tokens() + (judge_usage.total_tokens() if judge_usage else 0),
            "cost_usd": usage.cost(pricing) + (judge_usage.cost(pricing) if judge_usage else 0.0),
        }
        _write_json(self.out_dir / "summary.json", summary)
        _write_lines(self.out_dir / "cases.jsonl", self.cases)
        _write_lines(self.out_dir / "errors.jsonl", self.errors)
        (self.out_dir / "report.md").write_text(_report(self.suite, summary), encoding="utf-8")
        return self.out_dir


def compare(first: Path, second: Path) -> list[CompareRow]:
    """Each shared metric marked improved / regressed / within noise (evaluation §9.1).

    E-CLS-1 uses a paired bootstrap of the difference over the cases both runs scored; other metrics
    compare their stored 95% CIs, and are within noise unless the intervals don't overlap.
    """
    summary_a, summary_b = _read_json(first / "summary.json"), _read_json(second / "summary.json")
    rows = []
    for metric_id in sorted(set(summary_a["metrics"]) & set(summary_b["metrics"])):
        value_a, value_b = summary_a["metrics"][metric_id]["value"], summary_b["metrics"][metric_id]["value"]
        if metric_id == "E-CLS-1":
            verdict = _paired_verdict(first, second)
        else:
            verdict = _ci_verdict(
                summary_a["metrics"][metric_id]["ci95"], summary_b["metrics"][metric_id]["ci95"]
            )
        rows.append(CompareRow(metric_id, value_a, value_b, verdict))
    return rows


def _paired_verdict(first: Path, second: Path) -> str:
    cases_a = {
        case["case_id"]: case for case in _read_lines(first / "cases.jsonl") if not case["unexpected_model"]
    }
    cases_b = {
        case["case_id"]: case for case in _read_lines(second / "cases.jsonl") if not case["unexpected_model"]
    }
    shared = sorted(set(cases_a) & set(cases_b))
    gold = {key: cases_a[key]["gold"] for key in shared}
    classes = sorted(
        {*gold.values(), *(cases_a[key]["pred"] for key in shared), *(cases_b[key]["pred"] for key in shared)}
    )
    low, high = paired_bootstrap_ci(
        gold,
        {key: cases_a[key]["pred"] for key in shared},
        {key: cases_b[key]["pred"] for key in shared},
        classes=classes,
    )
    return _verdict(low, high)


def _ci_verdict(ci_a: list[float] | None, ci_b: list[float] | None) -> str:
    if not ci_a or not ci_b:
        return "within noise"
    if ci_b[0] > ci_a[1]:
        return "improved"
    return "regressed" if ci_b[1] < ci_a[0] else "within noise"


def _verdict(low: float, high: float) -> str:
    if low > 0:
        return "improved"
    return "regressed" if high < 0 else "within noise"


def _report(suite: str, summary: dict[str, Any]) -> str:
    rows = ["| Metric | Value | 95% CI | Target | Gate | Pass |", "|---|---|---|---|---|---|"]
    for metric_id, metric in summary["metrics"].items():
        value = "n/a" if metric["value"] is None else f"{metric['value']:.3f}"
        ci = f"{metric['ci95'][0]:.3f}–{metric['ci95'][1]:.3f}" if metric["ci95"] else ""
        target = "" if metric["target"] is None else f"{metric['target']}"
        rows.append(f"| {metric_id} | {value} | {ci} | {target} | {metric['gate']} | {metric['pass']} |")
    counts = (
        f"Scored {summary['scored']} of {summary['attempts']} cases; {summary['errors']} errors; "
        f"{summary['unexpected_model']} served by an unexpected model (excluded from the headline). "
        f"Tokens: {summary['tokens_total']:,}; cost ${summary['cost_usd']:.2f}."
    )
    return "\n".join([f"# Eval: {suite}", "", counts, "", *rows, ""])


def _git_sha() -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    except OSError:
        return "unknown"
    return result.stdout.strip() or "unknown"


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_lines(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
