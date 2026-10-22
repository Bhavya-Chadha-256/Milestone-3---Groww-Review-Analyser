"""The ingest stage: sources → scrubbed, marked, deduplicated reviews in SQLite (architecture.md §3.1–3.2)."""

import hashlib
import platform
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from pulse.ingest.base import ImportReport
from pulse.ingest.normalize import mark_duplicates, mark_exclusions, normalize, purge_raw
from pulse.models import RawReview, RunStatus
from pulse.pipeline import RunContext, register_stage
from pulse.privacy.scrub import Scrubber
from pulse.timeutil import format_range, window_bounds_utc


@register_stage("ingest")
def ingest(context: RunContext) -> RunStatus:
    since, until = window_bounds_utc(context.window)
    raws, imports, failed = _fetch_sources(context, since, until)
    if context.sources and len(failed) == len(context.sources):
        context.report.sections["Import"] = "Every source failed; nothing was imported."
        return RunStatus.FAILED
    settings = context.settings
    scrubber = Scrubber(settings.privacy.extra_patterns)
    reviews, exclusions = mark_exclusions(
        [normalize(raw, scrubber) for raw in raws], settings.ingest.min_words, settings.ingest.languages
    )
    reviews = mark_duplicates(reviews)
    upserted = context.store.upsert_reviews(reviews)
    purged = [] if failed else _purge(context)
    counts = {
        "imported": len(reviews),
        "new": upserted.new,
        "updated": upserted.updated,
        "skipped": sum(report.skipped.total() for report in imports.values()),
        "duplicates_across_files": sum(report.duplicates for report in imports.values()),
        "duplicate_text": sum(review.is_duplicate_text for review in reviews),
        "excluded": dict(sorted(exclusions.items())),
        "eligible": sum(review.excluded_reason is None for review in reviews),
        "purged_raw_files": len(purged),
    }
    context.report.counts.update(counts)
    context.report.sections["Import"] = _import_section(imports, failed, counts, exclusions)
    context.report.sections["Window"] = _window_section(context)
    context.save_artifact("manifest.json", _manifest(context, imports, counts))
    return RunStatus.INGESTED


def _fetch_sources(
    context: RunContext, since: datetime, until: datetime
) -> tuple[list[RawReview], dict[str, ImportReport], list[str]]:
    raws: list[RawReview] = []
    imports: dict[str, ImportReport] = {}
    failed: list[str] = []
    for source in context.sources:
        try:
            raws.extend(source.fetch(since, until))
        except Exception:  # one failing source must not sink the others (ING-17)
            failed.append(source.name)
            context.report.warnings.append(f"source_failed:{source.name}")
            continue
        imports[source.name] = source.report
        context.report.warnings.extend(source.report.warnings)
    return raws, imports, failed


def _purge(context: RunContext) -> list[Path]:
    """Purge only raw files a source actually read; nothing at all when no file source ran."""
    read_paths = [path for source in context.sources for path in getattr(source, "read_paths", [])]
    settings = context.settings
    return purge_raw(
        Path(settings.paths.raw), settings.retention.raw_days, _now(context), read_paths=read_paths
    )


def _now(context: RunContext) -> datetime:
    return context.opts.started_at or datetime.now(UTC)


def _window_section(context: RunContext) -> str:
    window = context.window
    return (
        f"- Reported week: {window.iso_week}\n"
        f"- Window: {format_range(window.start, window.end)} ({context.settings.window.weeks} weeks, "
        f"{window.timezone})"
    )


def _import_section(
    imports: dict[str, ImportReport], failed: list[str], counts: dict, exclusions: Counter[str]
) -> str:
    lines = []
    for name, report in imports.items():
        skipped = (
            ", ".join(f"{reason}: {count}" for reason, count in sorted(report.skipped.items())) or "none"
        )
        lines.append(
            f"- Source `{name}`: {report.rows_read} rows read, {report.imported} imported, "
            f"{report.duplicates} duplicates across files; skipped: {skipped}"
        )
    lines += [f"- Source `{name}`: failed" for name in failed]
    excluded = ", ".join(f"{reason}: {count}" for reason, count in sorted(exclusions.items())) or "none"
    lines += [
        f"- Stored: {counts['new']} new, {counts['updated']} updated, {counts['imported']} in window",
        f"- Excluded from themes (stored and counted): {excluded}",
        f"- Eligible for themes: {counts['eligible']}",
        f"- Duplicate text: {counts['duplicate_text']}",
        f"- Raw files purged (older than retention): {counts['purged_raw_files']}",
    ]
    return "\n".join(lines)


def _manifest(context: RunContext, imports: dict[str, ImportReport], counts: dict) -> dict:
    settings = context.settings
    return {
        "run_id": context.run_id,
        "window": asdict(context.window),
        "window_utc": [bound.isoformat() for bound in window_bounds_utc(context.window)],
        "sources": {
            name: {
                "rows_read": report.rows_read,
                "imported": report.imported,
                "duplicates": report.duplicates,
                "skipped": dict(report.skipped),
                "warnings": report.warnings,
            }
            for name, report in imports.items()
        },
        "counts": counts,
        "config_hash": hashlib.sha256(settings.model_dump_json().encode()).hexdigest()[:16],
        "versions": {"pulse": version("pulse"), "python": platform.python_version()},
    }
