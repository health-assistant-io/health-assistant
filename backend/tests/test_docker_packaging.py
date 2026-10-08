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

RUNTIME_COPY_PATHS = (
    "backend/app",
    "backend/alembic",
    "backend/alembic.ini",
    "backend/scripts",
    "backend/data",
    "integrations",
)

RUNTIME_DATA_FILES = (
    "backend/data/seeds/omb_race_ethnicity.json",
    "backend/data/seeds/default_catalog.json",
    "backend/data/seeds/sample_blood_panel.pdf",
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
