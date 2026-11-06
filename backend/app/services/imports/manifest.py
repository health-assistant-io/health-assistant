"""Manifest verification for export bundles."""

from app.services.imports.types import (
    BackupManifest,
    hashlib,
    zipfile,
)


class ManifestMixin:
    # ---------------- manifest verification ----------------

    @staticmethod
    def verify_manifest_from_zip(
        zf: zipfile.ZipFile,
    ) -> tuple[bool, BackupManifest | None, list[str]]:
        errors: list[str] = []
        try:
            manifest_bytes = zf.read("manifest.json")
        except KeyError:
            return False, None, ["manifest.json not found in archive"]
        try:
            manifest = BackupManifest.model_validate_json(manifest_bytes)
        except Exception as e:
            return False, None, [f"Invalid manifest.json: {e}"]

        all_ok = True
        for f in manifest.files:
            try:
                data = zf.read(f.path)
            except KeyError:
                errors.append(f"File listed in manifest missing from archive: {f.path}")
                all_ok = False
                continue
            actual = hashlib.sha256(data).hexdigest()
            if actual != f.sha256:
                errors.append(f"SHA256 mismatch for {f.path}")
                all_ok = False
        return all_ok, manifest, errors
