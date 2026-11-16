"""Docker packaging guards — runtime data paths must ship in the images.

2026-10 outage class: the Dockerfile COPY allowlist omitted ``backend/data``,
so production containers had no seed JSONs — GET /setup/extension-catalog
returned empty race/ethnicity/language options (the OMB picklist is read from
disk at request time) and every SeedService stage warned "seed file not
found" while seeding nothing. These guards pin the allowlist so a path the
app reads at runtime cannot silently drop out of the image again.
"""

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent

RUNTIME_IMAGE_ALLOWLIST = (
    "docker/Dockerfile",
    "docker/Dockerfile.worker",
)

# Bundled extensions ship inside backend/app (the extension subsystem);
# the bridge SDK satellites under integrations/ are consumer packages the
# backend never imports — they stay out of the runtime image.
RUNTIME_COPY_PATHS = (
    "backend/app",
    "backend/alembic",
    "backend/alembic.ini",
    "backend/scripts",
    "backend/data",
    "contracts",
)

RUNTIME_DATA_FILES = (
    "backend/data/seeds/omb_race_ethnicity.json",
    "backend/data/seeds/default_catalog.json",
    "backend/data/seeds/sample_blood_panel.pdf",
)

# Files the extension registry / data-lifecycle read from /app/contracts at
# runtime. The image build failed production startup while every test passed
# from a checkout (2026-11-07).
RUNTIME_CONTRACT_FILES = (
    "contracts/extension-manifest.schema.json",
    "contracts/docs-tree.schema.json",
    "contracts/extension-charter.json",
    "contracts/data-model.lifecycle.txt",
)


def test_runtime_images_copy_backend_data():
    for dockerfile in RUNTIME_IMAGE_ALLOWLIST:
        content = (REPO_ROOT / dockerfile).read_text()
        runtime_stage = content.split("FROM base AS runtime", 1)[1]
        for path in RUNTIME_COPY_PATHS:
            assert f"COPY {path} " in runtime_stage, (
                f"{dockerfile} must COPY {path} into the runtime stage"
            )


def test_runtime_data_files_exist_in_repo():
    for rel in RUNTIME_DATA_FILES:
        assert (REPO_ROOT / rel).is_file(), f"runtime data file missing: {rel}"


def test_runtime_contract_files_exist_in_repo():
    for rel in RUNTIME_CONTRACT_FILES:
        assert (REPO_ROOT / rel).is_file(), f"runtime contract file missing: {rel}"


def _service_block(text: str, name: str) -> str:
    marker = f"\n  {name}:\n"
    start = text.index(marker)
    rest = text[start + len(marker) :]
    # Stop at the next top-level service (two-space indent + name + colon).
    for line in rest.splitlines():
        if line.startswith("  ") and not line.startswith("    ") and line.rstrip().endswith(":"):
            return rest[: rest.index(line)]
    return rest


def test_worker_services_mount_the_uploads_volume():
    """Local OCR/DICOM processors read the backend's document bytes.

    The worker is a separate container; without the shared uploads volume a
    local deployment cannot materialize a document and every OCR task fails.
    The cloud overlay removes the mount again for stateless object storage.
    """
    for compose in (
        "docker/docker-compose.prod.yml",
        "docker/docker-compose.standalone.yml",
        "docker/docker-compose.dev.yml",
    ):
        text = (REPO_ROOT / compose).read_text()
        worker = _service_block(text, "worker")
        assert "uploads:/app/uploads" in worker, f"{compose}: worker has no uploads volume"


def test_cloud_overlay_removes_the_uploads_volume():
    text = (REPO_ROOT / "docker/docker-compose.cloud.yml").read_text()
    assert "docker-compose.prod.yml" in text
    for service in ("backend", "worker"):
        block = _service_block(text, service)
        assert "volumes: !reset []" in block, (
            f"cloud overlay must reset the {service} uploads volume"
        )


def test_cloud_overlay_requires_profile_and_backend():
    """A cloud deployment cannot boot on empty interpolation defaults."""
    text = (REPO_ROOT / "docker/docker-compose.cloud.yml").read_text()
    for service in ("backend", "worker"):
        block = _service_block(text, service)
        assert "HA_CLOUD_PROFILE: ${HA_CLOUD_PROFILE:?" in block, (
            f"{service} must require HA_CLOUD_PROFILE"
        )
        assert "HA_OBJECT_STORAGE_BACKEND: ${HA_OBJECT_STORAGE_BACKEND:?" in block, (
            f"{service} must require HA_OBJECT_STORAGE_BACKEND"
        )
        assert 'HA_OBJECT_STORAGE_REQUIRED: "true"' in block


def test_backup_sidecar_is_database_only_under_object_storage():
    text = (REPO_ROOT / "docker/docker-compose.prod.yml").read_text()
    backup = _service_block(text, "backup")
    assert "pg_dump" in backup
    assert "HA_OBJECT_STORAGE_BACKEND" in backup, "backup must see the deployed backend"
    assert "tar czf /backups/uploads-" in backup
    assert "skipping the uploads volume" in backup


def test_worker_and_backend_images_install_both_provider_extras():
    for dockerfile in ("docker/Dockerfile", "docker/Dockerfile.worker"):
        text = (REPO_ROOT / dockerfile).read_text()
        assert "--extra object-storage-s3" in text, f"{dockerfile}: missing S3 extra"
        assert "--extra object-storage-azure" in text, f"{dockerfile}: missing Azure extra"
