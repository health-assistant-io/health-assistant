"""OCR import rides the scoped AI gateway — no ad-hoc model construction.

Audit finding (P0): ``ImportService.import_from_ocr`` built its own chat model
from caller-supplied ``api_key``/``api_base``/``model`` values fed from
``settings.OPENAI_*``, bypassing task resolution and the cached BYOK key that
every other model consumer obeys. The call site now resolves the ``ocr`` task
assignment through ``AIProviderService.get_ocr_processor`` (the gateway), and
the request carries no model configuration at all.
"""

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.enums import ImportStatus
from app.schemas.user import TokenData


@pytest.mark.asyncio
async def test_import_from_ocr_resolves_through_gateway(tmp_path: Path):
    """The processor comes from the gateway; the model factory is never hit."""
    from app.services.imports.service import ImportService

    file_path = tmp_path / "scan.png"
    file_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    processor = MagicMock()
    processor.extract_text = AsyncMock(return_value="# extracted text")
    resolve = AsyncMock(return_value=processor)

    svc = ImportService.__new__(ImportService)
    svc.db = MagicMock()

    tenant_id = uuid4()
    user_id = uuid4()
    with (
        patch(
            "app.ai.providers.service.AIProviderService.get_ocr_processor",
            new=resolve,
        ),
        patch(
            "app.ai.chat_models.build_openai",
            side_effect=AssertionError("OCR import constructed a chat model directly"),
        ),
    ):
        result = await svc.import_from_ocr(
            file_path=file_path,
            tenant_id=str(tenant_id),
            user_id=str(user_id),
        )

    assert result.status == ImportStatus.COMPLETED
    resolve.assert_awaited_once_with(tenant_id, user_id)
    processor.extract_text.assert_awaited_once_with(file_path)


@pytest.mark.asyncio
async def test_import_from_ocr_reports_gateway_failure(tmp_path: Path):
    """A gateway resolution failure surfaces as a failed import, not a crash."""
    from app.services.imports.service import ImportService

    file_path = tmp_path / "scan.png"
    file_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    svc = ImportService.__new__(ImportService)
    svc.db = MagicMock()

    with patch(
        "app.ai.providers.service.AIProviderService.get_ocr_processor",
        new=AsyncMock(side_effect=RuntimeError("no OCR assignment")),
    ):
        result = await svc.import_from_ocr(file_path=file_path, tenant_id=str(uuid4()))

    assert result.status == ImportStatus.FAILED
    assert any("no OCR assignment" in e for e in result.errors)


def test_import_from_ocr_accepts_no_model_credentials():
    """Signature guard: identity + file only, never provider/model/key values."""
    from app.services.imports.service import ImportService

    params = set(inspect.signature(ImportService.import_from_ocr).parameters)
    assert params == {"self", "file_path", "tenant_id", "patient_id", "user_id"}


def test_ocr_endpoint_accepts_no_model_configuration():
    """The HTTP surface must not forward client-chosen model configuration."""
    from app.api.v1.endpoints import import_data

    params = set(inspect.signature(import_data.import_ocr).parameters)
    for forbidden in ("api_base", "api_key", "model", "model_name", "extract_tables"):
        assert forbidden not in params, f"/import/ocr still accepts {forbidden!r}"


def test_import_path_never_constructs_chat_models():
    """Source guard: the import call site builds no LangChain chat model."""
    import importlib
    import pkgutil

    import app.services.imports as imports_pkg
    from app.api.v1.endpoints import import_data
    from app.services.imports import service as import_service

    modules = [import_service, import_data] + [
        importlib.import_module(f"app.services.imports.{mi.name}")
        for mi in pkgutil.iter_modules(imports_pkg.__path__)
        if mi.name != "service"
    ]

    for module in modules:
        src = inspect.getsource(module)
        for marker in (
            "chat_models.build_",
            "from langchain_openai",
            "from langchain_anthropic",
            "ChatOpenAI(",
        ):
            assert marker not in src, f"{module.__name__} constructs a chat model ({marker})"


@pytest.mark.asyncio
async def test_ocr_endpoint_passes_caller_identity(async_client):
    """The endpoint forwards the caller's tenant/user to the gateway path."""
    from app.api.v1.endpoints import import_data
    from app.core.security import get_current_user
    from app.main import app
    from app.schemas.import_data import ImportResult

    tenant_id = uuid4()
    user_id = uuid4()
    current_user = TokenData(
        user_id=user_id,
        tenant_id=tenant_id,
        role="USER",
        sub="user@example.com",
    )

    async def _override():
        return current_user

    app.dependency_overrides[get_current_user] = _override
    import_mock = AsyncMock(
        return_value=ImportResult(
            job_id="",
            status=ImportStatus.COMPLETED,
            total_records=1,
            processed_records=1,
            failed_records=0,
        )
    )
    try:
        with patch.object(import_data.ImportService, "import_from_ocr", new=import_mock):
            response = await async_client.post(
                "/api/v1/import/ocr",
                files={"file": ("scan.png", b"\x89PNG\r\n\x1a\n", "image/png")},
            )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 200, response.text
    assert response.json()["status"] == ImportStatus.COMPLETED.value
    kwargs = import_mock.await_args.kwargs
    assert kwargs["tenant_id"] == str(tenant_id)
    assert kwargs["user_id"] == str(user_id)
    assert not {"api_key", "api_base", "model", "config"} & set(kwargs)
