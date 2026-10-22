"""The human-readable run report, runs/<run_id>/report.md (architecture.md §8). Counts and reasons only."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RunReport:
    warnings: list[str] = field(default_factory=list)
    counts: dict[str, Any] = field(default_factory=dict)
    sections: dict[str, str] = field(default_factory=dict)

    def render(self, title: str) -> str:
        parts = [f"# {title}"]
        parts += [f"## {name}\n\n{body.strip()}" for name, body in self.sections.items()]
        warnings = "\n".join(f"- {warning}" for warning in self.warnings) or "None."
        parts.append(f"## Warnings\n\n{warnings}")
        return "\n\n".join(parts) + "\n"

    def write(self, path: Path, title: str = "Run report") -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render(title), encoding="utf-8")
        return path
