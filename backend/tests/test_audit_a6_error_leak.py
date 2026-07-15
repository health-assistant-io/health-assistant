"""Tests for audit item A6 — stack-trace / internal leak via ``detail=str(e)``.

Broad ``except Exception`` blocks must not forward the raw exception message to
the client (it can leak DB schema, constraint names, file paths, or PHI). The
global exception handler in ``app/main.py`` returns a generic 500 + correlation
id instead; endpoints should either re-raise or return a generic detail.

These tests statically inspect the source of the previously-leaky endpoints so
the regression cannot silently return.
"""
import inspect

import pytest

from app.api.v1.endpoints import (
    admin,
    ai_assistance,
    biomarkers,
    documents_db,
    import_data,
)


# Endpoints that previously leaked ``str(e)`` (audit A6 reference list).
LEAKY_ENDPOINTS = [
    documents_db.trigger_extraction_endpoint,
    documents_db.upload_temp_preview,
    documents_db.get_dicom_metadata_endpoint,
    documents_db.get_document_preview_endpoint,
    import_data.import_backup,
    ai_assistance.assist_user,
]


@pytest.mark.parametrize("fn", LEAKY_ENDPOINTS, ids=lambda fn: fn.__name__)
def test_endpoint_does_not_leak_str_e(fn):
    src = inspect.getsource(fn)
    assert "detail=str(e)" not in src, (
        f"{fn.__name__} still leaks raw exception via detail=str(e)"
    )
    assert "{str(e)}" not in src, (
        f"{fn.__name__} still embeds str(e) in an f-string detail"
    )
    assert "{e}" not in src or "logger" in src, (
        f"{fn.__name__} embeds {{e}} in a client-facing string"
    )


def test_documents_upload_does_not_echo_tenant_id():
    """The tenant-not-found path must not echo the tenant_id value back."""
    src = inspect.getsource(documents_db.upload_document_endpoint)
    assert '"Tenant not found: {tenant_id}"' not in src
    assert "Tenant not found" in src  # generic message still present


def test_biomarker_endpoints_use_logger_not_str_e():
    """The biomarker CRUD cluster (7 sites) must log server-side, not leak."""
    for fn_name in [
        "create_unit",
        "create_biomarker",
        "update_biomarker",
        "delete_biomarker",
    ]:
        fn = getattr(biomarkers, fn_name, None)
        if fn is None:
            continue
        src = inspect.getsource(fn)
        assert "detail=str(e)" not in src, f"{fn_name} leaks str(e)"


def test_admin_catalog_import_does_not_leak_on_broad_except():
    src = inspect.getsource(admin.import_catalog_from_file)
    # The broad except must not embed the exception in the client detail.
    assert 'detail=f"Invalid catalog payload: {e}"' not in src


def test_global_handler_returns_generic_in_prod(monkeypatch):
    """The global handler must never surface str(exc) unless DEBUG is on."""
    from app.main import global_exception_handler

    class _Req:
        method = "POST"
        url = type("U", (), {"path": "/x"})()

    async def _runner(debug: bool):
        monkeypatch.setattr("app.main.settings.DEBUG", debug)
        resp = await global_exception_handler(_Req(), Exception("secret db internals: password='pw'"))
        import json

        body = json.loads(resp.body.decode())
        return body

    import asyncio

    body = asyncio.run(_runner(False))
    assert "secret db internals" not in body["detail"]
    assert "correlation_id" in body

    body_dbg = asyncio.run(_runner(True))
    # In DEBUG the detail IS surfaced for developer convenience.
    assert "secret db internals" in body_dbg["detail"]
