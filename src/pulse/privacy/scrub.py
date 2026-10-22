"""India-aware PII detection and redaction (architecture.md §3.2, edgecase §3).

Patterns run in a fixed order. Each match is masked before the next pattern runs, so a later pattern
can never re-match part of an earlier one, and match offsets always refer to the original text.
"""

import re
from dataclasses import dataclass, field

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("url", re.compile(r"https?://\S+")),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    (
        "email",  # obfuscated, e.g. "name [at] gmail [dot] com" (PII-6)
        re.compile(
            r"[\w.+-]+\s*(?:\[at\]|\(at\)|\sat\s)\s*[\w-]+\s*(?:\[dot\]|\(dot\)|\sdot\s)\s*[a-z]{2,}",
            re.IGNORECASE,
        ),
    ),
    ("upi", re.compile(r"[\w.-]{2,}@[A-Za-z]{2,}(?!\w|\.\w)")),  # a sentence-ending period is fine
    ("pan", re.compile(r"\b[A-Z]{5}\s?\d{4}\s?[A-Z]\b")),
    (
        "pan",
        re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.IGNORECASE),
    ),  # unspaced only: spaced + any case hits prose
    ("ifsc", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
    ("aadhaar", re.compile(r"(?<!\d)[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")),
    ("phone", re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9](?:[\s-]?\d){9}(?!\d)")),  # any 1-char grouping
    ("account", re.compile(r"(?<!\d)\d{9,18}(?!\d)")),
)
DIGIT_KINDS = frozenset({"aadhaar", "phone", "account"})
CURRENCY_BEFORE = re.compile(r"(?:₹|(?<![A-Za-z])(?:rs\.?|inr))\s*$", re.IGNORECASE)  # not "users", "hrs"
PLACEHOLDER_RE = re.compile(r"\[(?:email|phone|pan|aadhaar|upi|ifsc|account|url|id)\]")
MASK = "\x00"  # a non-word, non-digit stand-in for already-matched characters


@dataclass(frozen=True)
class PiiMatch:
    kind: str
    start: int
    end: int


@dataclass(frozen=True)
class ScrubResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)


def find_pii(text: str, extra_patterns: list[str] | None = None) -> list[PiiMatch]:
    """Every PII span in `text`, in position order, with offsets into `text`."""
    patterns = [*PATTERNS, *(("id", re.compile(pattern)) for pattern in extra_patterns or [])]
    masked = text
    matches: list[PiiMatch] = []
    for kind, pattern in patterns:
        for found in pattern.finditer(masked):
            if kind in DIGIT_KINDS and CURRENCY_BEFORE.search(text[: found.start()]):
                continue  # a rupee amount, not an identifier (PII-2)
            matches.append(PiiMatch(kind, found.start(), found.end()))
        for match in matches:
            masked = masked[: match.start] + MASK * (match.end - match.start) + masked[match.end :]
    return sorted(matches, key=lambda match: match.start)


class Scrubber:
    def __init__(self, extra_patterns: list[str] | None = None) -> None:
        self.extra_patterns = list(extra_patterns or [])

    def scrub(self, text: str) -> ScrubResult:
        """Replace each PII span with its `[kind]` placeholder and count the replacements by kind."""
        counts: dict[str, int] = {}
        scrubbed = text
        for match in reversed(find_pii(text, self.extra_patterns)):
            scrubbed = scrubbed[: match.start] + f"[{match.kind}]" + scrubbed[match.end :]
            counts[match.kind] = counts.get(match.kind, 0) + 1
        return ScrubResult(scrubbed, dict(sorted(counts.items())))
