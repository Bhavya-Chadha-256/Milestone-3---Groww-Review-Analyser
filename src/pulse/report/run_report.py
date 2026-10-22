"""The human-readable run report, runs/<run_id>/report.md (architecture.md §8). Counts and reasons only."""

import json
from dataclasses import asdict, dataclass, field
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
