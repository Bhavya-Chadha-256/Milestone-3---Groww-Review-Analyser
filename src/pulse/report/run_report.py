"""The human-readable run report, runs/<run_id>/report.md (architecture.md §8). Counts and reasons only."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Warnings from reports saved before warnings were tagged by stage; they always describe the stage
# that a resume reruns.
UNTAGGED_TRANSIENT = ("daily_token_budget", "stage_failed:")


@dataclass
class RunReport:
    warnings: list[str] = field(default_factory=list)
    counts: dict[str, Any] = field(default_factory=dict)
    sections: dict[str, str] = field(default_factory=dict)
    warning_stages: dict[str, str] = field(default_factory=dict)  # warning → the stage that raised it

    def render(self, title: str) -> str:
        parts = [f"# {title}"]
        parts += [f"## {name}\n\n{body.strip()}" for name, body in self.sections.items()]
        warnings = "\n".join(f"- {warning}" for warning in self.warnings) or "None."
        parts.append(f"## Warnings\n\n{warnings}")
        return "\n\n".join(parts) + "\n"

    def tag_warnings(self, stage: str, since: int) -> None:
        for warning in self.warnings[since:]:
            self.warning_stages.setdefault(warning, stage)

    def forget_stages(self, stages: list[str]) -> None:
        """Drop the warnings of stages a resume reruns; they re-add whatever still applies (OPS-11)."""
        self.warnings = [warning for warning in self.warnings if not self._from_rerun(warning, stages)]
        self.warning_stages = {
            warning: stage for warning, stage in self.warning_stages.items() if stage not in stages
        }

    def _from_rerun(self, warning: str, stages: list[str]) -> bool:
        stage = self.warning_stages.get(warning)
        return stage in stages if stage else warning.startswith(UNTAGGED_TRANSIENT)

    def save_state(self, path: Path) -> Path:
        """Persist sections, warnings and counts, so a resumed run keeps earlier sections (OPS-11)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), indent=2, default=str, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return path

    @classmethod
    def load_state(cls, path: Path) -> "RunReport":
        path = Path(path)
        return cls(**json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else cls()

    def write(self, path: Path, title: str = "Run report") -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render(title), encoding="utf-8")
        return path
