"""Anatomy figure keys vs the pre-contract ``anatomy_figures/`` prefix."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.anatomy_model import AnatomyFigure
from app.services.anatomy import anatomy_service
from app.storage.bootstrap import ENTRYPOINT_SERVER, StorageConfig
from app.storage.object_storage import LocalObjectStore


def _store(tmp_path) -> LocalObjectStore:
    return LocalObjectStore(
        StorageConfig(
            backend="local",
            entrypoint=ENTRYPOINT_SERVER,
            local_root=tmp_path / "objects",
            max_object_mb=5,
        ).validate()
    )


def test_legacy_anatomy_prefix_is_a_path_not_a_key(tmp_path, monkeypatch) -> None:
    legacy_root = tmp_path / "uploads"
    legacy = legacy_root / "anatomy_figures" / "man-front.webp"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy webp")
    monkeypatch.setattr(
        "app.services.anatomy.anatomy_service.settings.HA_UPLOAD_DIR", str(legacy_root)
    )
    store = _store(tmp_path)
    monkeypatch.setattr("app.storage.documents.current_store", lambda settings=None: store)
    monkeypatch.setattr("app.storage.artifacts.current_store", lambda settings=None: store)

    figure = AnatomyFigure(id=uuid4(), image_path="anatomy_figures/man-front.webp")
    key, path = anatomy_service.figure_image_key(figure)
    assert key is None
    assert path == legacy
    assert anatomy_service.read_figure_image(figure) == b"legacy webp"


def test_jpg_figure_uses_jpeg_content_type(tmp_path, monkeypatch) -> None:
    class RecordingStore(LocalObjectStore):
        content_type: str | None = None

        def put(self, key, data, *, content_type=None, metadata=None):
            self.content_type = content_type
            return super().put(key, data, content_type=content_type, metadata=metadata)

    store = RecordingStore(
        StorageConfig(
            backend="local",
            entrypoint=ENTRYPOINT_SERVER,
            local_root=tmp_path / "objects",
            max_object_mb=5,
        ).validate()
    )
    monkeypatch.setattr("app.storage.documents.current_store", lambda settings=None: store)

    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (2, 2), (1, 2, 3)).save(buf, format="JPEG")
    key, w, h = anatomy_service.save_figure_image("heart", buf.getvalue(), ext="jpg")
    assert key.endswith(".jpg")
    assert store.content_type == "image/jpeg"
    assert (w, h) == (2, 2)


def test_keyed_figure_is_not_mistaken_for_a_path(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr("app.storage.artifacts.current_store", lambda settings=None: store)
    key = anatomy_service.figure_key_for("heart", "webp")
    store.put(key, b"keyed webp")
    figure = AnatomyFigure(id=uuid4(), image_path=key)
    assert anatomy_service.figure_image_key(figure) == (key, None)
    assert anatomy_service.read_figure_image(figure) == b"keyed webp"


@pytest.mark.asyncio
async def test_seed_anatomy_skips_unchanged_objects(tmp_path, monkeypatch) -> None:
    from io import BytesIO

    from PIL import Image

    from app.core.database import AsyncSessionLocal
    from app.services.seed.anatomy import AnatomySeedMixin

    seeds = tmp_path / "seeds" / "anatomy_figures"
    seeds.mkdir(parents=True)
    buf = BytesIO()
    Image.new("RGB", (2, 2), (1, 2, 3)).save(buf, format="WEBP")
    for name in ("man-front.webp", "man-back.webp", "woman-front.webp", "woman-back.webp"):
        (seeds / name).write_bytes(buf.getvalue())

    puts: list[str] = []

    class RecordingStore(LocalObjectStore):
        def put(self, key, data, *, content_type=None, metadata=None):
            puts.append(key)
            return super().put(key, data, content_type=content_type, metadata=metadata)

    store = RecordingStore(
        StorageConfig(
            backend="local",
            entrypoint=ENTRYPOINT_SERVER,
            local_root=tmp_path / "objects",
            max_object_mb=5,
        ).validate()
    )
    monkeypatch.setattr("app.storage.documents.current_store", lambda settings=None: store)
    monkeypatch.setattr("app.storage.artifacts.current_store", lambda settings=None: store)

    class Seeder(AnatomySeedMixin):
        def __init__(self) -> None:
            self.seeds_dir = tmp_path / "seeds"

        async def _table_exists(self, _session, _name):
            return True

        def note_item_error(self, *_args, **_kwargs):
            pass

    async with AsyncSessionLocal() as db:
        seeder = Seeder()
        first = await seeder.seed_anatomy_figures(db)
        after_first = len(puts)
        second = await seeder.seed_anatomy_figures(db)
        await db.rollback()

    assert first["errors"] == 0
    assert second["errors"] == 0
    assert second["unchanged"] == 4
    assert len(puts) == after_first, "unchanged figures must not be re-uploaded"

