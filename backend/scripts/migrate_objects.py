"""Copy health's legacy upload tree into the configured object store.

Family tooling for plan 55 P4 (see ``docs/dev/storage.md``). Health keeps
absolute paths in ``documents.file_path`` and pre-contract relative locations
for anatomy figures, exports and seed-pack caches. This tool maps each one to
the key the runtime already uses:

* `<upload_dir>/<file>` -> `tenants/<tenant>/documents/<id><ext>`
* `<upload_dir>/anatomy_figures/<slug>[-source].<ext>`
  -> `tenants/system/anatomy/<slug>[-source].<ext>`
* `<upload_dir>/exports/<job-id>[.<stem>].<ext>` -> the export key
* `<upload_dir>/seed-packs/<source-id>/<digest>.zip` -> the seed-pack key

Anything without a stable owner/tenant key (an import archive, an unexpected
export name) is reported as skipped — regenerable or re-uploadable, never
guessed. Documents, anatomy rows and export jobs are backfilled only after a
clean `--verify`.

Usage (run from ``backend/`` with the project environment active):

    python scripts/migrate_objects.py --dry-run
    python scripts/migrate_objects.py --apply
    python scripts/migrate_objects.py --verify
    python scripts/migrate_objects.py --backfill-db

The manifest defaults to `<upload_dir>/../migrations/object-storage-manifest.json`.
Never run this on a desktop build (health is server-only). The target store is
the resolved `HA_*` configuration for this process; point it at the
destination without changing the running app's environment.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import AsyncSessionLocal
from app.models.anatomy_model import AnatomyFigure
from app.models.document_model import DocumentModel
from app.models.export_import_job import ExportJobModel
from app.services.anatomy.anatomy_service import figure_key_for
from app.storage.artifacts import (
    export_key,
    export_manifest_key,
    guess_content_type,
    seed_pack_key,
)
from app.storage.bootstrap import resolve_storage_config
from app.storage.documents import document_key
from app.storage.object_storage import BACKEND_LOCAL, build_store
from app.storage.object_storage_migrate import (
    STATUS_MISSING,
    STATUS_SKIPPED,
    STATUS_VERIFIED,
    Manifest,
    ManifestError,
    ManifestTargetMismatch,
    MigrationEntry,
    TargetNotEmpty,
    VerifyRequired,
    bind_target,
    check_binding,
    load_manifest,
    merge_entries,
    new_manifest,
    plan_entries,
    rebind_manifest,
    require_verified,
    save_manifest,
    target_descriptor,
)
from app.storage.object_storage_migrate import (
    apply as apply_entries,
)
from app.storage.object_storage_migrate import (
    verify as verify_entries,
)

HOST = "health-assistant"
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_EXPORT_NAME = re.compile(rf"^({_UUID})(?:\.(fhir|catalog))?\.(zip|json)$")
_SEED_PACK = re.compile(rf"^({_UUID})/([0-9a-f]{{64}})\.zip$")
_PROBLEM_STATUSES = {"failed", "missing", "unreadable", "collision", "skipped"}
_BLOCKING_STATUSES = {"failed", "missing", "unreadable", "collision"}


def default_manifest_path(settings: Settings) -> Path:
    return Path(settings.HA_UPLOAD_DIR).parent / "migrations" / "object-storage-manifest.json"


@dataclass
class Planned:
    entries: list[MigrationEntry]
    media: dict[str, str] = field(default_factory=dict)


def _is_object_key(value: str) -> bool:
    return value.startswith(("tenants/", "sha256/"))


def _relative(
    root: Path,
    raw: str | None,
    *,
    legacy_prefixes: tuple[str, ...] = (),
    allow_relative: bool = False,
) -> str | None:
    """A path under the source root as a relative POSIX string, else ``None``.

    Document paths may be absolute or a bare relative legacy name
    (``scan.pdf``); anatomy and export paths are relative with a known
    legacy prefix. A value that already starts with ``tenants/``/``sha256/``
    is an object key and is never treated as a filesystem path.
    """
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        if _is_object_key(raw):
            return None
        if not allow_relative and not any(raw.startswith(pre) for pre in legacy_prefixes):
            return None
        path = root / path
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _figure_key(value: str, slug: str, *, source: bool) -> str | None:
    extension = Path(value).suffix.lstrip(".")
    if not slug or not extension:
        return None
    return figure_key_for(slug, extension, source=source)


def _export_key(tenant_id, job_id, filename: str) -> str | None:
    if filename == f"{job_id}.manifest.json":
        return export_manifest_key(tenant_id, job_id)
    match = _EXPORT_NAME.match(filename)
    if match is None or uuid.UUID(match.group(1)) != job_id:
        return None
    stem, extension = match.group(2), match.group(3)
    if stem is None and extension == "zip":
        return export_key(tenant_id, job_id)
    if stem is not None and extension == "json":
        return export_key(tenant_id, job_id, "json", stem=stem)
    return None


def build_plan(
    settings: Settings,
    documents: list[DocumentModel],
    figures: list[AnatomyFigure],
    exports: list[ExportJobModel],
) -> Planned:
    root = Path(settings.HA_UPLOAD_DIR)
    mapping: dict[str, str] = {}
    media: dict[str, str] = {}
    expected: list[tuple[str, str]] = []
    skipped: dict[str, str] = {}

    for document in documents:
        key = document_key(document.tenant_id, document.id, document.filename)
        relative = _relative(root, document.file_path, allow_relative=True)
        if relative is None:
            if not document.object_key:
                skipped[f"external/{document.id}"] = (
                    "file_path is outside HA_UPLOAD_DIR or already a key; "
                    "re-upload the document instead of guessing"
                )
            continue
        mapping[relative] = key
        media[key] = guess_content_type(document.filename)
        expected.append((relative, key))

    for figure in figures:
        for raw, is_source in (
            (figure.image_path, False),
            (figure.source_image_path, True),
        ):
            if not raw:
                continue
            legacy_relative = raw.startswith("anatomy_figures/")
            if not legacy_relative and _is_object_key(raw):
                continue
            key = _figure_key(raw, figure.slug, source=is_source)
            relative = _relative(root, raw, legacy_prefixes=("anatomy_figures/",))
            if key is None or relative is None:
                if raw:
                    skipped[f"external/anatomy-{figure.id}"] = (
                        "anatomy figure is outside HA_UPLOAD_DIR or unnamed; regenerate the figure"
                    )
                continue
            mapping[relative] = key
            media[key] = guess_content_type(relative)
            expected.append((relative, key))

    seed_dir = root / "seed-packs"
    if seed_dir.is_dir():
        for pack in sorted(seed_dir.glob("*/*.zip")):
            relative = pack.relative_to(root).as_posix()
            match = _SEED_PACK.match(relative.removeprefix("seed-packs/"))
            if match is None:
                skipped[relative] = "seed pack is not content-addressed; regenerate it"
                continue
            key = seed_pack_key(uuid.UUID(match.group(1)), match.group(2))
            mapping[relative] = key
            media[key] = "application/zip"
            expected.append((relative, key))

    jobs = {job.id: job for job in exports}
    exports_dir = root / "exports"
    if exports_dir.is_dir():
        for artifact in sorted(exports_dir.glob("*")):
            if not artifact.is_file() or artifact.name.startswith("."):
                continue
            relative = artifact.relative_to(root).as_posix()
            job_id = None
            try:
                job_id = uuid.UUID(artifact.name[:36])
            except ValueError:
                job_id = None
            job = jobs.get(job_id) if job_id is not None else None
            key = _export_key(job.tenant_id, job.id, artifact.name) if job else None
            if key is None:
                skipped[relative] = "no stable export job/tenant row; the export is regenerable"
                continue
            mapping[relative] = key
            media[key] = "application/zip" if artifact.suffix == ".zip" else "application/json"
            expected.append((relative, key))

    imports_dir = root / "imports"
    if imports_dir.is_dir():
        for archive in sorted(imports_dir.glob("*")):
            if archive.is_file() and not archive.name.startswith("."):
                skipped[archive.relative_to(root).as_posix()] = (
                    "import archives are re-uploadable; no stable object key"
                )

    entries = plan_entries(root, lambda relative: mapping.get(relative))
    planned_sources = {entry.source for entry in entries}
    for relative, key in expected:
        if relative in planned_sources:
            continue
        entries.append(
            MigrationEntry(
                source=relative,
                key=key,
                status=STATUS_MISSING,
                error="source file is missing",
            )
        )
    for entry in entries:
        if entry.source in skipped:
            entry.key = ""
            entry.status = STATUS_SKIPPED
            entry.error = skipped[entry.source]
    for source, error in skipped.items():
        if source not in {entry.source for entry in entries}:
            entries.append(
                MigrationEntry(source=source, key="", status=STATUS_SKIPPED, error=error)
            )
    return Planned(entries=entries, media=media)


def _report(manifest: Manifest, label: str) -> None:
    counts = manifest.counts()
    print(
        f"{label}: {counts['total']} entries — "
        f"copied {counts['copied']}, verified {counts['verified']}, "
        f"pending {counts['pending']}, failed {counts['failed']}, "
        f"missing {counts['missing']}, unreadable {counts['unreadable']}, "
        f"collision {counts['collision']}, skipped {counts['skipped']}"
    )
    problems = [entry for entry in manifest.entries if entry.status in _PROBLEM_STATUSES]
    for entry in problems[:20]:
        print(f"  {entry.status}: {entry.source} — {entry.error}")
    if len(problems) > 20:
        print(f"  … {len(problems) - 20} more")


async def load_rows(
    session: AsyncSession,
) -> tuple[list[DocumentModel], list[AnatomyFigure], list[ExportJobModel]]:
    documents = list((await session.scalars(select(DocumentModel))).all())
    figures = list((await session.scalars(select(AnatomyFigure))).all())
    exports = list((await session.scalars(select(ExportJobModel))).all())
    return documents, figures, exports


async def backfill(session: AsyncSession, manifest: Manifest) -> dict[str, int]:
    """Rewrite verified document/anatomy/export locations to object keys."""
    verified = manifest.keys({STATUS_VERIFIED})
    updated_documents = 0
    updated_figures = 0
    updated_exports = 0

    documents = list((await session.scalars(select(DocumentModel))).all())
    for document in documents:
        key = document_key(document.tenant_id, document.id, document.filename)
        if key in verified and document.object_key != key:
            document.object_key = key
            session.add(document)
            updated_documents += 1

    figures = list((await session.scalars(select(AnatomyFigure))).all())
    for figure in figures:
        changed = False
        for attribute, is_source in (
            ("image_path", False),
            ("source_image_path", True),
        ):
            value = getattr(figure, attribute, None)
            if not value:
                continue
            if not value.startswith("anatomy_figures/") and _is_object_key(value):
                continue
            key = _figure_key(value, figure.slug, source=is_source)
            if key is not None and key in verified:
                setattr(figure, attribute, key)
                changed = True
        if changed:
            session.add(figure)
            updated_figures += 1

    exports = list((await session.scalars(select(ExportJobModel))).all())
    for job in exports:
        changed = False
        for attribute in ("file_path", "manifest_path"):
            value = getattr(job, attribute, None)
            if not value or _is_object_key(value):
                continue
            key = _export_key(job.tenant_id, job.id, Path(value).name)
            if key is not None and key in verified:
                setattr(job, attribute, key)
                changed = True
        if changed:
            session.add(job)
            updated_exports += 1

    await session.commit()
    return {
        "documents": updated_documents,
        "figures": updated_figures,
        "exports": updated_exports,
    }


async def _plan_or_load(
    session: AsyncSession,
    settings: Settings,
    manifest_path: Path,
    *,
    force: bool,
    store,
    rebind: bool,
) -> tuple[Manifest, dict[str, str]]:
    source = Path(settings.HA_UPLOAD_DIR)
    documents, figures, exports = await load_rows(session)
    planned = build_plan(settings, documents, figures, exports)
    if manifest_path.exists():
        try:
            manifest = load_manifest(manifest_path)
        except ManifestError as exc:
            if not force:
                raise SystemExit(
                    f"corrupt manifest {manifest_path}: {exc}. "
                    "Move it aside or pass --force to start a fresh ledger."
                ) from exc
            print(f"ignoring corrupt manifest (--force): {exc}")
            manifest = new_manifest(HOST, source, path=manifest_path)
            bind_target(manifest, store)
        else:
            if rebind:
                rebind_manifest(manifest, store, host=HOST, source_root=source)
            else:
                check_binding(manifest, store, host=HOST, source_root=source)
    else:
        manifest = new_manifest(HOST, source, path=manifest_path)
        bind_target(manifest, store)
    merge_entries(manifest, planned.entries)
    manifest.path = str(manifest_path)
    return manifest, planned.media


def _refuse_self_copy(settings: Settings, store) -> None:
    if store.backend != BACKEND_LOCAL:
        return
    source = Path(settings.HA_UPLOAD_DIR).resolve()
    target = Path(getattr(store, "root", source)).resolve()
    if source == target:
        raise SystemExit(
            f"source and target are the same local root ({source}); nothing to copy. "
            "Set HA_OBJECT_STORAGE_LOCAL_ROOT to a staging directory or run this "
            "process with the target object backend configured."
        )
    if source in target.parents or target in source.parents:
        raise SystemExit(
            f"source ({source}) and target ({target}) roots are nested; use "
            "separate directory trees so a copy can never read its own output."
        )


async def run_async(
    args: argparse.Namespace,
    settings: Settings,
    *,
    session_factory=None,
) -> int:
    factory = session_factory or AsyncSessionLocal
    manifest_path = Path(args.manifest) if args.manifest else default_manifest_path(settings)
    config = resolve_storage_config(settings)
    store = build_store(config)
    print(f"target: {target_descriptor(store).label()}")
    try:
        if args.verify:
            try:
                manifest = load_manifest(manifest_path)
            except FileNotFoundError:
                raise SystemExit(f"no manifest at {manifest_path}; run --apply first") from None
            except ManifestError as exc:
                raise SystemExit(f"corrupt manifest {manifest_path}: {exc}") from exc
            source = Path(settings.HA_UPLOAD_DIR)
            try:
                if args.rebind:
                    rebind_manifest(manifest, store, host=HOST, source_root=source)
                    save_manifest(manifest_path, manifest)
                else:
                    check_binding(manifest, store, host=HOST, source_root=source)
            except ManifestTargetMismatch as exc:
                raise SystemExit(str(exc)) from exc
            summary = verify_entries(store, manifest, host=HOST, source_root=source)
            _report(manifest, "verify")
            if summary["blocking"]:
                print(
                    f"blocking: {summary['blocking']} entry/entries — {summary['blocking_entries']}"
                )
            return 0 if summary["blocking"] == 0 else 1

        if args.backfill_db:
            try:
                manifest = load_manifest(manifest_path)
            except FileNotFoundError:
                raise SystemExit(
                    f"no manifest at {manifest_path}; run --apply and --verify first"
                ) from None
            except ManifestError as exc:
                raise SystemExit(f"corrupt manifest {manifest_path}: {exc}") from exc
            source = Path(settings.HA_UPLOAD_DIR)
            try:
                if args.rebind:
                    rebind_manifest(manifest, store, host=HOST, source_root=source)
                    save_manifest(manifest_path, manifest)
                else:
                    check_binding(manifest, store, host=HOST, source_root=source)
                require_verified(manifest)
            except (ManifestTargetMismatch, VerifyRequired) as exc:
                raise SystemExit(str(exc)) from exc
            async with factory() as session:
                counts = await backfill(session, manifest)
            print(
                "backfill: documents {documents}, figures {figures}, exports {exports}".format(
                    **counts
                )
            )
            return 0

        try:
            async with factory() as session:
                manifest, media = await _plan_or_load(
                    session,
                    settings,
                    manifest_path,
                    force=args.force,
                    store=store,
                    rebind=args.rebind,
                )
        except ManifestTargetMismatch as exc:
            raise SystemExit(str(exc)) from exc
        _refuse_self_copy(settings, store)
        source = Path(settings.HA_UPLOAD_DIR)
        if not args.dry_run:
            save_manifest(manifest_path, manifest)
        try:
            summary = apply_entries(
                store,
                manifest,
                dry_run=args.dry_run,
                force=args.force,
                content_type_for=lambda entry: media.get(entry.key),
                host=HOST,
                source_root=source,
            )
        except TargetNotEmpty as exc:
            raise SystemExit(str(exc)) from exc
        _report(manifest, "dry run" if args.dry_run else "apply")
        print(
            f"this run: copied {summary['copied']}, remaining {summary['remaining']}, "
            f"pending {summary['pending']}, foreign {summary['foreign']}"
        )
        print(f"manifest: {manifest_path}")
        if args.dry_run:
            print(f"would copy {summary['planned']} object(s)")
            if summary["foreign"]:
                print(
                    f"warning: target prefix has {summary['foreign']} foreign object(s); "
                    "--apply will require --force"
                )
        blocked = any(entry.status in _BLOCKING_STATUSES for entry in manifest.entries)
        return 1 if summary["failed"] or blocked else 0
    finally:
        store.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate health's legacy uploads into the configured object store."
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--dry-run", action="store_true", help="plan and report only")
    action.add_argument("--apply", action="store_true", help="copy into the target store")
    action.add_argument("--verify", action="store_true", help="re-read the target and compare")
    action.add_argument(
        "--backfill-db",
        action="store_true",
        help="write keys back to documents/anatomy/export rows (after --verify)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="manifest path (default: <upload_dir>/../migrations/object-storage-manifest.json)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="allow a non-empty target prefix or replace a corrupt manifest",
    )
    parser.add_argument(
        "--rebind",
        action="store_true",
        help="adopt the current target; resets transferred entries to pending",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not (args.dry_run or args.apply or args.verify or args.backfill_db):
        args.dry_run = True
    from app.core.config import settings

    return asyncio.run(run_async(args, settings))


if __name__ == "__main__":
    sys.exit(main())
