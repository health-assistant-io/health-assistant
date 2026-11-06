"""CSP is served — on every API response and by the SPA's serving config.

Audit finding (P0): ``HeaderPolicy(web_csp=None)`` claimed the CSP lived in
nginx, but ``docker/nginx.conf`` carried no ``add_header`` and the SPA is
served by ``serve`` (``docker/Dockerfile.frontend``), which emitted nothing.
The policy now lives in ``app.core.csp.DOCUMENT_CSP`` for API responses and in
``frontend/public/serve.json`` for the production SPA image; the dev server
carries the HMR-compatible variant. These tests pin the header, its scope, and
the prod copies together.
"""

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = REPO_ROOT / "frontend"


def _directives(policy: str) -> dict[str, tuple[str, ...]]:
    """Parse a CSP header value into ``{directive: (source, ...)}``."""
    parsed: dict[str, tuple[str, ...]] = {}
    for part in policy.split(";"):
        tokens = part.strip().split()
        if tokens:
            parsed[tokens[0]] = tuple(tokens[1:])
    return parsed


def _serve_json_csp() -> str:
    config = json.loads((FRONTEND / "public" / "serve.json").read_text())
    for rule in config["headers"]:
        for header in rule["headers"]:
            if header["key"].lower() == "content-security-policy":
                return header["value"]
    raise AssertionError("frontend/public/serve.json carries no Content-Security-Policy")


@pytest.mark.asyncio
async def test_api_responses_carry_csp(async_client):
    """Every API response carries a CSP with the baseline directives."""
    response = await async_client.get("/health")
    assert response.status_code == 200
    csp = response.headers.get("content-security-policy")
    assert csp, "API responses must carry a Content-Security-Policy"
    directives = _directives(csp)
    assert directives["default-src"] == ("'self'",)
    assert directives["script-src"] == ("'self'",), "productions scripts are same-origin only"
    assert directives["object-src"] == ("'none'",)
    assert directives["base-uri"] == ("'self'",)
    assert directives["connect-src"] == ("'self'",)


@pytest.mark.asyncio
async def test_csp_has_no_external_origins_or_wildcards(async_client):
    """Single-sourced, self-hosted assets: no external origins, no wildcards."""
    csp = (await async_client.get("/health")).headers["content-security-policy"]
    assert "http://" not in csp and "https://" not in csp
    for directive in _directives(csp).values():
        for source in directive:
            assert source != "*"
            assert not source.startswith("*.")


@pytest.mark.asyncio
async def test_csp_keeps_same_origin_document_preview(async_client):
    """The inline PDF preview is a same-origin iframe (XFO SAMEORIGIN pair)."""
    csp = (await async_client.get("/health")).headers["content-security-policy"]
    directives = _directives(csp)
    assert directives["frame-ancestors"] == ("'self'",)
    assert "blob:" in directives["img-src"]
    assert "blob:" in directives["media-src"]


@pytest.mark.asyncio
async def test_security_headers_survive_the_csp_mount(async_client):
    """The header matrix keeps the family baseline besides the CSP."""
    response = await async_client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_production_spa_policy_matches_the_api_policy():
    """serve.json (prod SPA) and app.core.csp (API) must not drift."""
    from app.core.csp import DOCUMENT_CSP

    assert _directives(_serve_json_csp()) == _directives(DOCUMENT_CSP)


def test_production_spa_policy_is_strict():
    """The shipped SPA policy is the strict prod variant, not the dev one."""
    directives = _directives(_serve_json_csp())
    assert directives["script-src"] == ("'self'",)
    assert directives["connect-src"] == ("'self'",)
    assert directives["frame-ancestors"] == ("'self'",)


def test_index_html_has_no_inline_script():
    """Inline scripts would be blocked by the shipped script-src 'self'."""
    html = (FRONTEND / "index.html").read_text()
    for match in re.finditer(r"<script\b[^>]*>", html):
        assert "src=" in match.group(0), "index.html must not inline scripts under the CSP"


def test_dev_server_policy_keeps_hmr_working():
    """The dev surface is the only place 'unsafe-inline'/ws: are allowed."""
    config = (FRONTEND / "vite.config.ts").read_text()
    assert "securityHeaders" in config
    assert "headers: securityHeaders" in config
    dev_block = config.split("const devCsp =", 1)[1].split("].join", 1)[0]
    assert "script-src 'self' 'unsafe-inline'" in dev_block
    assert "connect-src 'self' ws: wss:" in dev_block


def test_nginx_does_not_duplicate_the_spa_csp():
    """The frontend image's serve.json is the single prod source for the SPA."""
    nginx = (REPO_ROOT / "docker" / "nginx.conf").read_text()
    assert "add_header Content-Security-Policy" not in nginx
