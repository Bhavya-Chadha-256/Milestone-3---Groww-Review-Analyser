import csv
import io
import logging
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import dashboard_client
from pydantic import ValidationError
from test_cli import invoke
from web_data import OCT, SAMPLE, at, review, write

from pulse.config import DashboardSettings, Settings
from pulse.web.queries import REVIEW_FIELDS

ENDPOINTS = (
    "/api/meta",
    "/api/summary",
    "/api/themes",
    "/api/trend",
    "/api/ratings",
    "/api/reviews",
    "/api/quality",
)


def with_dashboard(settings: Settings, **fields: Any) -> Settings:
    return settings.model_copy(update={"dashboard": settings.dashboard.model_copy(update=fields)})


def test_endpoints_return_json_for_default_filter(web_client: Any) -> None:
    for path in ENDPOINTS:
        response = web_client.get(path)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "no-store", path
        assert response.headers["x-content-type-options"] == "nosniff", path
    meta = web_client.get("/api/meta").json()
    assert meta["ready"] and meta["week"] == "2026-W42" and meta["window"]["start"] == "2026-07-27"
    assert [(theme["id"], theme["name"]) for theme in meta["themes"]] == [
        ("withdrawals", "Withdrawals"),
        ("charges", "Charges"),
    ]
    assert meta["themes"][0]["definition"] == "Withdrawals"
    assert meta["eligible"] == 7 and meta["total"] == 8 and "2026-W42" in meta["weeks"]
    assert web_client.get("/api/summary").json()["total"] == 7
    themes = web_client.get("/api/themes").json()
    assert [stats["theme_id"] for stats in themes] == ["withdrawals", "charges", "unthemed"]
    assert "arrow" in themes[0] and themes[2]["count"] == 1 and themes[2]["sentiment"] == {"positive": 1}
    page = web_client.get("/api/reviews", params={"ratings": [1, 2], "sort": "lowest_rating"}).json()
    assert page["total"] == 3 and [item["rating"] for item in page["items"]] == [1, 1, 2]


def test_invalid_filter_returns_422_without_echoing_value(web_client: Any) -> None:
    evil = "<b>evil</b>"
    response = web_client.get("/api/summary", params={"ratings": "9", "sources": evil, "nonsense": evil})
    assert response.status_code == 422
    assert sorted(response.json()["fields"]) == ["nonsense", "ratings", "sources"]
    assert evil not in response.text
    assert web_client.get("/api/summary", params={"week": evil}).json()["fields"] == ["week"]
    assert web_client.get("/api/reviews", params={"sort": "random"}).status_code == 422


def test_foreign_host_header_rejected(web_client: Any) -> None:  # DNS rebinding
    assert web_client.get("/api/meta", headers={"host": "evil.example:8787"}).status_code == 400
    assert web_client.get("/api/meta", headers={"host": "localhost:8787"}).status_code == 200


def test_api_never_writes_the_database(tmp_settings: Settings) -> None:
    db = Path(tmp_settings.paths.db)
    write(db)
    before = (db.read_bytes(), db.stat().st_mtime_ns)
    client = dashboard_client(tmp_settings)
    for path in (*ENDPOINTS, "/api/reviews.csv"):
        assert client.get(path).status_code == 200, path
    assert (db.read_bytes(), db.stat().st_mtime_ns) == before
    assert sorted(item.name for item in db.parent.iterdir()) == ["pulse.db"]


def test_missing_database_reports_not_ready(tmp_settings: Settings) -> None:
    client = dashboard_client(tmp_settings)
    assert client.get("/api/meta").json() == {"ready": False}
    assert client.get("/api/summary").status_code == 409
    assert not Path(tmp_settings.paths.db).exists()


def test_csv_export_neutralises_formulas_and_respects_cap(tmp_settings: Settings) -> None:
    formula = review("r10", posted_at=at(OCT, 16), text="=HYPERLINK(evil) and some more words here")
    write(Path(tmp_settings.paths.db), [*SAMPLE, formula])
    response = dashboard_client(tmp_settings).get("/api/reviews.csv")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    assert 'filename="reviews-2026-07-27-2026-10-18.csv"' in response.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(response.text)))
    assert tuple(rows[0]) == REVIEW_FIELDS and len(rows) == 9
    assert rows[1][REVIEW_FIELDS.index("text")].startswith("'=HYPERLINK")
    capped = dashboard_client(with_dashboard(tmp_settings, export_max_rows=2)).get("/api/reviews.csv")
    assert len(list(csv.reader(io.StringIO(capped.text)))) == 3


def test_csv_export_disabled_returns_403(tmp_settings: Settings) -> None:
    write(Path(tmp_settings.paths.db))
    client = dashboard_client(with_dashboard(tmp_settings, allow_export=False))
    assert client.get("/api/reviews.csv").status_code == 403


def test_settings_reject_non_loopback_host() -> None:
    with pytest.raises(ValidationError, match="loopback"):
        DashboardSettings(host="0.0.0.0")
    assert DashboardSettings(host="localhost").host == "localhost"
    assert DashboardSettings(host="::1").host == "::1"


def test_no_review_text_or_query_in_logs(
    web_client: Any, caplog: pytest.LogCaptureFixture
) -> None:  # E-WEB-3
    caplog.set_level(logging.DEBUG)
    assert web_client.get("/api/reviews", params={"query": "Fees"}).json()["total"] == 1
    server_side = [record.getMessage() for record in caplog.records if not record.name.startswith("httpx")]
    assert not any("Fees" in message or "query=" in message for message in server_side)


def test_serve_without_web_extra_prints_hint(  # craftsman-ignore: PY003 (hint on closing line)
    tmp_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for module in ("fastapi", "uvicorn", "pulse.web.app"):
        monkeypatch.setitem(sys.modules, module, None)  # makes `import` raise ImportError
    result = invoke(tmp_settings, tmp_path, "serve")
    assert result.exit_code == 2 and "uv sync --extra web" in result.output
