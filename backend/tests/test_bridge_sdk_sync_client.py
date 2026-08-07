"""Tests for the bridge Python SDK sync-client retry + export parity (Phase 6.1).

The sync ``HealthAssistantBridgeClient`` (requests-based) had **no retry**
while the async client + the TS client did (full-jitter backoff, 3 attempts).
``ClientExaminationRecord`` was defined but not re-exported from the package
``__init__`` (the README imported it from the top level → ``ImportError``).
These tests pin the fixes.
"""
import importlib.util
import pathlib
import sys
import types

import pytest

_SDK_ROOT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "integrations" / "health_assistant_bridge" / "python-sdk"
)
_PKG_DIR = _SDK_ROOT / "health_assistant_bridge"


def _ensure_package():
    """Ensure ``health_assistant_bridge`` is importable as a package with a
    ``__version__`` attribute, without exec'ing the full ``__init__`` (which
    would eagerly import the clients and fight our per-test loading)."""
    if "health_assistant_bridge" in sys.modules:
        return
    pkg = types.ModuleType("health_assistant_bridge")
    pkg.__path__ = [str(_PKG_DIR)]
    pkg.__version__ = "1.3.0"
    sys.modules["health_assistant_bridge"] = pkg


def _load_submodule(file_path: pathlib.Path, dotted_name: str):
    """Load ``file_path`` as ``health_assistant_bridge.<dotted_name>`` so its
    relative imports (``from . import __version__``, ``from .models``) resolve
    against the planted package."""
    _ensure_package()
    full_name = f"health_assistant_bridge.{dotted_name}"
    if full_name in sys.modules:  # cached from a prior test
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(
        full_name, file_path,
        submodule_search_locations=None,
    )
    assert spec and spec.loader, f"{file_path} not found"
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "health_assistant_bridge"
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


def _client_module():
    """Load the sync client (and its models dependency) into sys.modules."""
    _load_submodule(_PKG_DIR / "models.py", "models")
    _load_submodule(_PKG_DIR / "signing.py", "signing")
    return _load_submodule(_PKG_DIR / "client.py", "client")


# ---------------------------------------------------------------------------
# Retry behaviour (sync client now matches async + TS)
# ---------------------------------------------------------------------------


def test_sync_client_retries_transient_network_errors(monkeypatch):
    """Two network failures then success → client returns after retries."""
    client_mod = _client_module()
    client = client_mod.HealthAssistantBridgeClient(
        "https://ha.example", "00000000-0000-0000-0000-000000000001",
    )

    calls = {"n": 0}

    class _FakeResp:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise client_mod.requests.HTTPError(f"{self.status_code}")

    def _flaky_request(method, url, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise client_mod.requests.ConnectionError("flaky")
        return _FakeResp(200, {"status": "active", "integration_id": "x"})

    monkeypatch.setattr(client_mod.requests, "request", _flaky_request)
    monkeypatch.setattr(client_mod.time, "sleep", lambda _: None)  # no real waits

    status = client.get_status()
    assert status.status == "active"
    assert calls["n"] == 3, "should have retried twice then succeeded"


def test_sync_client_retries_5xx_then_succeeds(monkeypatch):
    client_mod = _client_module()
    client = client_mod.HealthAssistantBridgeClient("https://ha.example", "id")

    calls = {"n": 0}

    class _FakeResp:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise client_mod.requests.HTTPError(f"{self.status_code}")

    def _request(method, url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResp(503, {})
        return _FakeResp(200, {"status": "active", "integration_id": "x"})

    monkeypatch.setattr(client_mod.requests, "request", _request)
    monkeypatch.setattr(client_mod.time, "sleep", lambda _: None)

    status = client.get_status()
    assert status.status == "active"
    assert calls["n"] == 2


def test_sync_client_raises_after_max_retries(monkeypatch):
    client_mod = _client_module()
    client = client_mod.HealthAssistantBridgeClient(
        "https://ha.example", "id", max_retries=2,
    )

    def _always_fail(method, url, **kw):
        raise client_mod.requests.ConnectionError("down")

    monkeypatch.setattr(client_mod.requests, "request", _always_fail)
    monkeypatch.setattr(client_mod.time, "sleep", lambda _: None)

    with pytest.raises(client_mod.requests.ConnectionError):
        client.get_status()


def test_sync_client_does_not_retry_4xx(monkeypatch):
    """A 400/401/404 raises immediately — retrying won't help."""
    client_mod = _client_module()
    client = client_mod.HealthAssistantBridgeClient("https://ha.example", "id")

    calls = {"n": 0}

    class _FakeResp:
        status_code = 404

        def json(self):
            return {}

        def raise_for_status(self):
            raise client_mod.requests.HTTPError("404")

    def _request(method, url, **kw):
        calls["n"] += 1
        return _FakeResp()

    monkeypatch.setattr(client_mod.requests, "request", _request)
    monkeypatch.setattr(client_mod.time, "sleep", lambda _: None)

    with pytest.raises(client_mod.requests.HTTPError):
        client.get_status()
    assert calls["n"] == 1, "4xx must not be retried"


def test_sync_client_max_retries_default_is_three():
    client_mod = _client_module()
    assert client_mod.DEFAULT_MAX_RETRIES == 3
    client = client_mod.HealthAssistantBridgeClient("https://ha.example", "id")
    assert client.max_retries == 3


# ---------------------------------------------------------------------------
# Export parity: ClientExaminationRecord re-exported from the package
# ---------------------------------------------------------------------------


def test_client_examination_record_re_exported():
    """The package __init__ must re-export ClientExaminationRecord (the README
    imports it from the top level; pre-fix this raised ImportError)."""
    import ast

    init_src = (_SDK_ROOT / "health_assistant_bridge" / "__init__.py").read_text()
    assert "ClientExaminationRecord" in init_src, (
        "ClientExaminationRecord must be re-exported from the package __init__"
    )
    tree = ast.parse(init_src)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "ClientExaminationRecord" in imported
    # And in __all__.
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if getattr(target, "id", None) == "__all__":
                    assert "ClientExaminationRecord" in [
                        s.value for s in node.value.elts if isinstance(s, ast.Constant)
                    ]


def test_sdk_version_bumped_to_1_3_0():
    init_src = (_SDK_ROOT / "health_assistant_bridge" / "__init__.py").read_text()
    assert '__version__ = "1.3.0"' in init_src
