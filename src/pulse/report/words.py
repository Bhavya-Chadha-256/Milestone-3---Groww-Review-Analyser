"""Word counting and Markdown escaping for the pulse (architecture.md §3.8, Task 4.3)."""

import re

LINE_MARKERS = re.compile(r"^[ \t]*(?:#+|>|\d+\.)[ \t]*", re.MULTILINE)
EMPHASIS = re.compile(r"[*_\\]")
WORD = re.compile(r"[0-9A-Za-zऀ-ॿ][\w'’ऀ-ॿ-]*")
MARKDOWN_SPECIAL = re.compile(r"([\\*_#>\[\]`])")


def count_words(markdown: str) -> int:
    """Words a reader sees: heading, quote and list markers and emphasis characters don't count."""
    text = EMPHASIS.sub("", LINE_MARKERS.sub("", markdown))
    return len(WORD.findall(text))


def escape_md(text: str) -> str:
    """Backslash-escapes Markdown syntax; used for quotes in the Markdown render only (REN-6)."""
    return MARKDOWN_SPECIAL.sub(r"\\\1", text)
