import re
from pathlib import Path
from typing import Any

WEB = Path(__file__).resolve().parents[1] / "web"
APP_JS = (WEB / "app.js").read_text(encoding="utf-8") if (WEB / "app.js").is_file() else ""
INDEX = (WEB / "index.html").read_text(encoding="utf-8") if (WEB / "index.html").is_file() else ""
SCRIPT_HOSTS = ("https://cdnjs.cloudflare.com/", "https://cdn.tailwindcss.com/")


def test_index_and_assets_served(web_client: Any) -> None:
    page = web_client.get("/")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert "Review Pulse" in page.text and 'id="tab-analyse"' in page.text and 'id="tab-pulse"' in page.text
    for asset, kind in (("/app.js", "javascript"), ("/tailwind.config.js", "javascript")):
        response = web_client.get(asset)
        assert response.status_code == 200 and kind in response.headers["content-type"], asset


def route_paths(routes: list[Any]) -> set[str]:
    """Every registered path; FastAPI ≥ 0.15x keeps included routers nested (`original_router`)."""
    found: set[str] = set()
    for route in routes:
        nested = getattr(route, "original_router", None)
        found |= route_paths(nested.routes) if nested is not None else {getattr(route, "path", "")}
    return found


def test_app_js_calls_only_known_endpoints(web_client: Any) -> None:
    routes = {path for path in route_paths(web_client.app.routes) if path.startswith("/api")}
    called = set(re.findall(r"""["'`](/api/[\w./-]*)["'`]""", APP_JS))
    assert called, "app.js calls no endpoints"
    assert called <= routes, called - routes


def test_app_js_never_uses_inner_html() -> None:
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in APP_JS, sink
    assert "onclick=" not in INDEX and "<script>" not in INDEX  # no inline script: the CSP forbids it


def test_security_headers_present(web_client: Any) -> None:
    policy = web_client.get("/").headers["content-security-policy"]
    assert "default-src 'self'" in policy and "frame-ancestors 'none'" in policy
    script_src = next(part for part in policy.split(";") if part.strip().startswith("script-src")).split()
    assert sorted(script_src[1:]) == sorted(["'self'", *(host.rstrip("/") for host in SCRIPT_HOSTS)])
    assert web_client.get("/api/meta").headers["cache-control"] == "no-store"


def test_cdn_scripts_pinned_to_exact_versions() -> None:
    external = re.findall(r'<script[^>]+src="(https?://[^"]+)"', INDEX)
    assert len(external) == 2, external
    for url in external:
        assert url.startswith(SCRIPT_HOSTS), url
        assert re.search(r"/\d+\.\d+\.\d+(/|$)", url), f"not pinned: {url}"
