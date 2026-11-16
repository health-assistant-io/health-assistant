"""migrate-objects for health: documents, anatomy, exports and seed packs.

Runs against the local test database and a temp local target store — no cloud
credentials. Covers dry-run/apply/verify/resume, corrupt manifests, the
non-empty guard, a tampered target object, and the idempotent DB backfill of
`documents.object_key` plus anatomy/export locations.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.core.config import settings as base_settings
from app.models.anatomy_model import AnatomyFigure
from app.models.document_model import DocumentModel
from app.models.enums import ExportScope, ExportType, JobStatus
from app.models.export_import_job import ExportJobModel
from app.storage.artifacts import anatomy_key, export_key, seed_pack_key
from app.storage.documents import document_key
from tests._auth_helpers import create_user

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "migrate_objects.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("health_migrate_objects", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cli = _load_cli()

_CREATED: dict[str, set[uuid.UUID]] = {
    "documents": set(),
    "figures": set(),
    "exports": set(),
}


@pytest.fixture(autouse=True)
def only_rows_created_by_this_module(monkeypatch):
    """Plan/backfill this module's rows only (the test DB is shared)."""
    original = cli.load_rows

    async def filtered(session):
        documents, figures, exports = await original(session)
        return (
            [row for row in documents if row.id in _CREATED["documents"]],
            [row for row in figures if row.id in _CREATED["figures"]],
            [row for row in exports if row.id in _CREATED["exports"]],
        )

    monkeypatch.setattr(cli, "load_rows", filtered)


@pytest_asyncio.fixture(autouse=True)
async def clean_migration_rows():
    """Remove only the rows this module created (the test DB is shared)."""
    yield
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        if _CREATED["documents"]:
            await session.execute(
                delete(DocumentModel).where(DocumentModel.id.in_(_CREATED["documents"]))
            )
        if _CREATED["figures"]:
            await session.execute(
                delete(AnatomyFigure).where(AnatomyFigure.id.in_(_CREATED["figures"]))
            )
        if _CREATED["exports"]:
            await session.execute(
                delete(ExportJobModel).where(ExportJobModel.id.in_(_CREATED["exports"]))
            )
        await session.commit()


def _settings(tmp_path: Path):
    return base_settings.model_copy(
        update={
            "HA_UPLOAD_DIR": str(tmp_path / "uploads"),
            "HA_OBJECT_STORAGE_LOCAL_ROOT": str(tmp_path / "target"),
            "HA_OBJECT_STORAGE_BACKEND": "local",
            "HA_OBJECT_STORAGE_MAX_MB": 1024,
        }
    )


def _args(
    manifest: Path,
    *,
    dry_run=False,
    apply=False,
    verify=False,
    backfill=False,
    force=False,
    rebind=False,
):
    return cli.build_parser().parse_args(
        [
            *(["--dry-run"] if dry_run else []),
            *(["--apply"] if apply else []),
            *(["--verify"] if verify else []),
            *(["--backfill-db"] if backfill else []),
            *(["--force"] if force else []),
            *(["--rebind"] if rebind else []),
            "--manifest",
            str(manifest),
        ]
    )


async def _document(user, path: Path, filename: str = "scan.pdf") -> DocumentModel:
    from app.core.database import AsyncSessionLocal

    document = DocumentModel(
        filename=filename,
        file_path=str(path),
        owner_id=user.id,
        tenant_id=user.tenant_id,
        status="uploaded",
    )
    async with AsyncSessionLocal() as session:
        session.add(document)
        await session.commit()
        await session.refresh(document)
    _CREATED["documents"].add(document.id)
    return document


async def _export_job(user, path: Path, job_id: uuid.UUID | None = None) -> ExportJobModel:
    from app.core.database import AsyncSessionLocal

    job = ExportJobModel(
        **({"id": job_id} if job_id is not None else {}),
        user_id=user.id,
        tenant_id=user.tenant_id,
        scope=ExportScope.SYSTEM,
        export_type=ExportType.FULL_BACKUP,
        status=JobStatus.COMPLETED,
        progress=100,
        file_path=str(path),
        file_size_bytes=path.stat().st_size if path.is_absolute() else 0,
    )
    async with AsyncSessionLocal() as session:
        session.add(job)
        await session.commit()
        await session.refresh(job)
    _CREATED["exports"].add(job.id)
    return job


async def _anatomy(path: Path) -> AnatomyFigure:
    from app.core.database import AsyncSessionLocal

    figure = AnatomyFigure(
        slug=f"test-figure-{uuid.uuid4().hex[:8]}",
        label="Test figure",
        figure_key="test",
        view_key="front",
        image_path=f"anatomy_figures/{path.name}",
        width=10,
        height=10,
    )
    async with AsyncSessionLocal() as session:
        session.add(figure)
        await session.commit()
        await session.refresh(figure)
    _CREATED["figures"].add(figure.id)
    return figure


def _legacy_file(tmp_path: Path, relative: str, payload: bytes) -> Path:
    path = tmp_path / "uploads" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


async def test_dry_run_reports_without_writing(tmp_path: Path, capsys) -> None:
    settings = _settings(tmp_path)
    user = await create_user()
    path = _legacy_file(tmp_path, "scan.pdf", b"pdf bytes")
    document = await _document(user, path)
    manifest = tmp_path / "manifest.json"

    assert await cli.run_async(_args(manifest, dry_run=True), settings) == 0
    output = capsys.readouterr().out
    assert "would copy 1 object" in output
    assert not manifest.exists()
    assert not any(Path(settings.HA_OBJECT_STORAGE_LOCAL_ROOT).rglob("*"))
    assert str(settings.HA_UPLOAD_DIR) not in output
    assert str(document.id) not in output


async def test_apply_verify_resume_and_backfill(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    user = await create_user()
    document_path = _legacy_file(tmp_path, "scan.pdf", b"pdf bytes")
    document = await _document(user, document_path)
    export_id = uuid.uuid4()
    export_path = _legacy_file(tmp_path, f"exports/{export_id}.zip", b"zip")
    job = await _export_job(user, export_path, export_id)
    figure_file = _legacy_file(tmp_path, "anatomy_figures/chest-front.webp", b"webp")
    figure = await _anatomy(figure_file)
    manifest = tmp_path / "manifest.json"

    from app.storage.documents import DocumentStorage
    from app.storage.object_storage import LocalObjectStore, StorageConfig

    store = LocalObjectStore(
        StorageConfig(
            backend="local",
            entrypoint="server",
            local_root=Path(settings.HA_OBJECT_STORAGE_LOCAL_ROOT),
            max_object_mb=1024,
        ).validate()
    )
    # Dual-read before any copy: no key, no object, the legacy path serves it.
    assert DocumentStorage(store).read(document) == b"pdf bytes"

    assert await cli.run_async(_args(manifest, apply=True), settings) == 0
    document_key_value = document_key(document.tenant_id, document.id, document.filename)
    assert store.get(document_key_value) == b"pdf bytes"
    assert store.get(export_key(job.tenant_id, job.id)) == b"zip"
    assert store.get(anatomy_key(figure.slug, "webp")) == b"webp"

    assert await cli.run_async(_args(manifest, apply=True), settings) == 0
    assert await cli.run_async(_args(manifest, verify=True), settings) == 0

    assert await cli.run_async(_args(manifest, backfill=True), settings) == 0
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        refreshed = await session.get(DocumentModel, document.id)
        assert refreshed is not None
        assert refreshed.object_key == document_key_value
        assert DocumentStorage(store).read(refreshed) == b"pdf bytes"
        refreshed_job = await session.get(ExportJobModel, job.id)
        assert refreshed_job is not None
        assert refreshed_job.file_path == export_key(job.tenant_id, job.id)
        refreshed_figure = await session.get(AnatomyFigure, figure.id)
        assert refreshed_figure is not None
        assert refreshed_figure.image_path == anatomy_key(figure.slug, "webp")

    # Idempotent: nothing changes on a second backfill.
    assert await cli.run_async(_args(manifest, backfill=True), settings) == 0
    async with AsyncSessionLocal() as session:
        refreshed = await session.get(DocumentModel, document.id)
        assert refreshed is not None
        assert refreshed.object_key == document_key_value


async def test_export_without_job_is_skipped(tmp_path: Path, capsys) -> None:
    settings = _settings(tmp_path)
    user = await create_user()
    path = _legacy_file(tmp_path, "scan.pdf", b"pdf")
    await _document(user, path)
    _legacy_file(tmp_path, f"exports/{uuid.uuid4()}.zip", b"zip")
    manifest = tmp_path / "manifest.json"

    assert await cli.run_async(_args(manifest, apply=True), settings) == 0
    output = capsys.readouterr().out
    assert "skipped: exports/" in output
    assert "regenerable" in output


async def test_non_empty_target_refuses_until_forced(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    user = await create_user()
    path = _legacy_file(tmp_path, "scan.pdf", b"pdf")
    await _document(user, path)
    target = Path(settings.HA_OBJECT_STORAGE_LOCAL_ROOT)
    target.mkdir(parents=True)
    (target / "foreign.bin").write_bytes(b"live data")
    manifest = tmp_path / "manifest.json"

    with pytest.raises(SystemExit, match="Refusing"):
        await cli.run_async(_args(manifest, apply=True), settings)
    assert await cli.run_async(_args(manifest, apply=True, force=True), settings) == 0


async def test_corrupt_manifest_and_verify_tamper(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    user = await create_user()
    path = _legacy_file(tmp_path, "scan.pdf", b"pdf")
    document = await _document(user, path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{broken", encoding="utf-8")

    with pytest.raises(SystemExit, match="corrupt manifest"):
        await cli.run_async(_args(manifest, apply=True), settings)
    assert await cli.run_async(_args(manifest, apply=True, force=True), settings) == 0

    from app.storage.object_storage import LocalObjectStore, StorageConfig

    store = LocalObjectStore(
        StorageConfig(
            backend="local",
            entrypoint="server",
            local_root=Path(settings.HA_OBJECT_STORAGE_LOCAL_ROOT),
            max_object_mb=1024,
        ).validate()
    )
    store.put(document_key(document.tenant_id, document.id, document.filename), b"tampered")
    assert await cli.run_async(_args(manifest, verify=True), settings) == 1


async def test_seed_pack_is_mapped_and_import_is_skipped(tmp_path: Path, capsys) -> None:
    settings = _settings(tmp_path)
    user = await create_user()
    path = _legacy_file(tmp_path, "scan.pdf", b"pdf")
    await _document(user, path)
    source_id = uuid.uuid4()
    digest = "a" * 64
    pack = _legacy_file(tmp_path, f"seed-packs/{source_id}/{digest}.zip", b"pack")
    _legacy_file(tmp_path, "imports/backup.zip", b"import")
    manifest = tmp_path / "manifest.json"

    assert await cli.run_async(_args(manifest, apply=True), settings) == 0
    from app.storage.object_storage import LocalObjectStore, StorageConfig

    store = LocalObjectStore(
        StorageConfig(
            backend="local",
            entrypoint="server",
            local_root=Path(settings.HA_OBJECT_STORAGE_LOCAL_ROOT),
            max_object_mb=1024,
        ).validate()
    )
    assert store.get(seed_pack_key(source_id, digest)) == pack.read_bytes()
    output = capsys.readouterr().out
    assert "skipped: imports/backup.zip" in output
    assert not any("imports/" in info.key for info in store.list())


async def test_relative_document_and_export_paths_are_explicit(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    user = await create_user()
    _legacy_file(tmp_path, "scan.pdf", b"pdf")
    document = await _document(user, Path("scan.pdf"))
    export_id = uuid.uuid4()
    _legacy_file(tmp_path, f"exports/{export_id}.zip", b"zip")
    job = await _export_job(user, Path(f"exports/{export_id}.zip"), export_id)
    manifest = tmp_path / "manifest.json"

    assert await cli.run_async(_args(manifest, apply=True), settings) == 0
    assert await cli.run_async(_args(manifest, verify=True), settings) == 0
    assert await cli.run_async(_args(manifest, backfill=True), settings) == 0
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        refreshed = await session.get(DocumentModel, document.id)
        assert refreshed is not None
        assert refreshed.object_key == document_key(
            document.tenant_id, document.id, document.filename
        )
        refreshed_job = await session.get(ExportJobModel, job.id)
        assert refreshed_job is not None
        assert refreshed_job.file_path == export_key(job.tenant_id, job.id)


async def test_wrong_target_is_refused_and_rebind_recovers(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    user = await create_user()
    path = _legacy_file(tmp_path, "scan.pdf", b"pdf")
    await _document(user, path)
    manifest = tmp_path / "manifest.json"
    assert await cli.run_async(_args(manifest, apply=True), settings) == 0

    other = settings.model_copy(update={"HA_OBJECT_STORAGE_LOCAL_ROOT": str(tmp_path / "target-b")})
    with pytest.raises(SystemExit, match="manifest target does not match"):
        await cli.run_async(_args(manifest, apply=True), other)
    assert await cli.run_async(_args(manifest, apply=True, rebind=True), other) == 0
    assert await cli.run_async(_args(manifest, verify=True), other) == 0


async def test_nested_roots_are_refused(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    user = await create_user()
    path = _legacy_file(tmp_path, "scan.pdf", b"pdf")
    await _document(user, path)
    nested = settings.model_copy(
        update={"HA_OBJECT_STORAGE_LOCAL_ROOT": str(tmp_path / "uploads" / "nested")}
    )
    with pytest.raises(SystemExit, match="nested"):
        await cli.run_async(_args(tmp_path / "manifest.json", apply=True), nested)


async def test_verify_blocks_a_missing_target_object(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    user = await create_user()
    path = _legacy_file(tmp_path, "scan.pdf", b"pdf")
    document = await _document(user, path)
    manifest = tmp_path / "manifest.json"
    assert await cli.run_async(_args(manifest, apply=True), settings) == 0
    from app.storage.object_storage import LocalObjectStore, StorageConfig

    store = LocalObjectStore(
        StorageConfig(
            backend="local",
            entrypoint="server",
            local_root=Path(settings.HA_OBJECT_STORAGE_LOCAL_ROOT),
            max_object_mb=1024,
        ).validate()
    )
    store.delete(document_key(document.tenant_id, document.id, document.filename))
    assert await cli.run_async(_args(manifest, verify=True), settings) == 1


async def test_backfill_requires_verify(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    user = await create_user()
    path = _legacy_file(tmp_path, "scan.pdf", b"pdf")
    await _document(user, path)
    manifest = tmp_path / "manifest.json"

    assert await cli.run_async(_args(manifest, apply=True), settings) == 0
    with pytest.raises(SystemExit, match="requires a clean verify"):
        await cli.run_async(_args(manifest, backfill=True), settings)


async def test_manifest_is_the_documented_shape(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    user = await create_user()
    path = _legacy_file(tmp_path, "scan.pdf", b"pdf")
    await _document(user, path)
    manifest = tmp_path / "manifest.json"

    assert await cli.run_async(_args(manifest, apply=True), settings) == 0
    payload = json.loads(manifest.read_text())
    assert payload["host"] == "health-assistant"
    assert payload["version"] == "object-storage-migration/1"
    assert payload["source_root"] == str(Path(settings.HA_UPLOAD_DIR))
    assert payload["target"]["backend"] == "local"
