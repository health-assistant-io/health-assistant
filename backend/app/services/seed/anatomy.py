"""Body-part and anatomy-figure seeding (identity upsert — ADR 0055).

Anatomy structures carry an immutable qualified ``seed_key``
(``anatomy:<kebab-key>``) in the seed JSON; the row id is the
deterministic ``uuid5`` derived from it (the table itself has no
``seed_key`` column — identity lives in the id). Upsert is by id only,
``tenant_id IS NULL`` — the ``slug`` stays display data.

Transaction ownership (ADR 0056): the seeders take a required session,
flush, and never commit — the caller owns the transaction. The binary
figure WebPs stay bundled-only side inputs (documented; never pack items
in v1).
"""

import contextlib
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.seed_pack.bundled import bundled_stage_items
from app.models.anatomy_model import AnatomyFigure, AnatomyStructure
from app.models.enums import CodingSystem, ConceptKind
from app.services.catalog.concept_service import resolve_concept_by_slug
from app.services.seed.identity import (
    apply_values,
    find_identity_row,
    identity_id,
    item_label,
    require_seed_key,
)

logger = logging.getLogger(__name__)

_JSON_FIELDS = ("display",)
_OPTIONAL_FIELDS = ("standard_code", "description", "display")


def anatomy_values(
    item: dict[str, Any], class_concept_id: Any, standard_sys: Any
) -> dict[str, Any]:
    """The mutable fields of an anatomy seed node (class link only when resolved)."""
    values: dict[str, Any] = {
        "name": item["name"],
        "standard_system": standard_sys,
    }
    # Never nullify an existing class_concept_id on re-seed: if the concept
    # can't be resolved (e.g. its kind tag is missing from a prior partial
    # seed), keep the previously-resolved value rather than destroying it.
    if class_concept_id is not None:
        values["class_concept_id"] = class_concept_id
    for field in _OPTIONAL_FIELDS:
        if item.get(field) is not None:
            values[field] = item[field]
    return values


class AnatomySeedMixin:
    async def seed_body_parts(
        self,
        session: AsyncSession,
        items: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        """Sync anatomy structures from the bundled (or given) pack items.

        Reads the parsed ``anatomy_structures`` stage (nodes only). The
        anatomy hierarchy edges (PART_OF, SUPPLIED_BY, etc.) live in
        ``concept_edges.json`` and are seeded by :meth:`seed_concept_edges` —
        this stage never deletes or relinks rows. The caller owns the
        session and the transaction.
        """
        if items is None:
            items = bundled_stage_items("body_parts", self.seeds_dir)
        anatomy_data = {"nodes": list(items), "edges": []}
        return await self._process_body_parts(session, anatomy_data)

    async def _process_body_parts(
        self, session: AsyncSession, data: dict[str, Any]
    ) -> dict[str, int]:
        stats = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0}

        if not await self._table_exists(session, "anatomy_structures"):
            logger.error("Table 'anatomy_structures' does not exist. Skipping seeding.")
            return {
                "added": 0,
                "updated": 0,
                "unchanged": 0,
                "skipped": len(data.get("nodes", [])),
                "errors": 0,
            }

        slug_to_id = {}
        nodes = data.get("nodes", [])

        for item in nodes:
            try:
                # The anatomy table stores no ``seed_key`` column (the
                # identity lives in the id), so an export cannot carry one
                # back — derive it from the slug (the curated form is exactly
                # ``anatomy:<slug>``) and require the qualified form.
                item = {**item, "seed_key": item.get("seed_key") or f"anatomy:{item['slug']}"}
                qualified = require_seed_key(item, "anatomy")

                # Resolve the anatomy-class concept slug (lowercase, e.g.
                # ``organ``) to a ``class_concept_id``. The seed JSON carries
                # ``class_concept_slug`` directly (the lossless form).
                class_concept_id = None
                explicit_slug = item.get("class_concept_slug")
                if explicit_slug:
                    class_concept_id = await resolve_concept_by_slug(
                        session, explicit_slug, ConceptKind.ANATOMY_CLASS
                    )

                standard_sys = None
                if item.get("standard_system"):
                    with contextlib.suppress(ValueError):
                        standard_sys = CodingSystem(item["standard_system"])

                db_part = await find_identity_row(
                    session, AnatomyStructure, qualified, explicit_id=item.get("id")
                )

                if db_part:
                    changed = apply_values(
                        db_part,
                        anatomy_values(item, class_concept_id, standard_sys),
                        _JSON_FIELDS,
                    )
                    stats["updated" if changed else "unchanged"] += 1
                    slug_to_id[db_part.slug] = db_part.id
                else:
                    new_part = AnatomyStructure(
                        id=identity_id(qualified, item.get("id")),
                        name=item["name"],
                        slug=item["slug"],
                        class_concept_id=class_concept_id,
                        standard_system=standard_sys,
                        standard_code=item.get("standard_code"),
                        description=item.get("description"),
                        display=item.get("display"),
                        tenant_id=None,
                        created_at=datetime.now(UTC),
                    )
                    session.add(new_part)
                    await session.flush()
                    stats["added"] += 1
                    slug_to_id[new_part.slug] = new_part.id
            except Exception as e:
                self.note_item_error("body_parts", item, e)
                logger.error(f"Error seeding anatomy node {item_label(item)}: {e}")
                stats["errors"] += 1

        all_nodes_result = await session.execute(select(AnatomyStructure.slug, AnatomyStructure.id))
        for slug, node_id in all_nodes_result.all():
            slug_to_id[slug] = node_id

        # Anatomy hierarchy edges (PART_OF, SUPPLIED_BY, etc.) are seeded by
        # seed_concept_edges from concept_edges.json (src_type=anatomy,
        # dst_type=anatomy).

        await session.flush()
        return stats

    async def seed_anatomy_figures(self, session: AsyncSession) -> dict[str, int]:
        """Seed the four default body figures (man/woman x front/back).

        The WebP files are bundled-only side inputs under
        ``data/seeds/anatomy_figures/`` (binary — never pack items in v1);
        each file is stored as an object (``tenants/system/anatomy/``) and
        the DB row records its key + pixel dimensions. Wikimedia surface
        diagrams, CC BY-SA 3.0 (see NOTICE).

        Idempotent: inserts missing rows and refreshes the image file when
        the seed changes. Markers are normalized 0-1 against pixel
        dimensions. The caller owns the session and the transaction (flush
        only, no commit).
        """
        spec = [
            ("man-front", "man-front.webp", "Male \u2014 Front", "man", "front", 0),
            ("man-back", "man-back.webp", "Male \u2014 Back", "man", "back", 1),
            (
                "woman-front",
                "woman-front.webp",
                "Female \u2014 Front",
                "woman",
                "front",
                2,
            ),
            ("woman-back", "woman-back.webp", "Female \u2014 Back", "woman", "back", 3),
        ]
        seeds_dir = Path(self.seeds_dir) / "anatomy_figures"
        from app.services.anatomy.anatomy_service import (
            figure_key_for,
            figure_object_exists,
            save_figure_image,
        )

        local = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0}
        if not await self._table_exists(session, "anatomy_figures"):
            logger.error("Table 'anatomy_figures' does not exist. Skipping figure seeding.")
            local["errors"] = 1
            return local
        import anyio

        for slug, fname, label, fkey, vkey, order in spec:
            try:
                src = seeds_dir / fname
                if not src.exists():
                    logger.warning(f"Figure seed not found: {src}")
                    local["errors"] += 1
                    continue
                data = src.read_bytes()
                ext = src.suffix.lstrip(".") or "webp"
                key = figure_key_for(slug, ext)
                from io import BytesIO

                from PIL import Image as PILImage

                with PILImage.open(BytesIO(data)) as img:
                    w, h = img.size
                existing_res = await session.execute(
                    select(AnatomyFigure).where(AnatomyFigure.slug == slug)
                )
                existing = existing_res.scalar_one_or_none()
                if existing:
                    object_present = await anyio.to_thread.run_sync(
                        figure_object_exists, slug, ext
                    )
                    if (
                        existing.image_path == key
                        and existing.width == w
                        and existing.height == h
                        and object_present
                    ):
                        local["unchanged"] += 1
                        continue
                # Store the seed figure as an object (system/tenant prefix);
                # the DB row keeps the key, never a path. Unchanged objects
                # are skipped, not re-uploaded on every boot.
                rel_path, w, h = await anyio.to_thread.run_sync(
                    save_figure_image, slug, data, ext
                )
                if existing:
                    existing.image_path = rel_path
                    existing.width, existing.height = w, h
                    existing.label = label
                    existing.sort_order = order
                    local["updated"] += 1
                    continue
                session.add(
                    AnatomyFigure(
                        id=uuid4(),
                        slug=slug,
                        label=label,
                        figure_key=fkey,
                        view_key=vkey,
                        image_path=rel_path,
                        width=w,
                        height=h,
                        sort_order=order,
                        is_active=True,
                        created_at=datetime.now(UTC),
                    )
                )
                local["added"] += 1
            except Exception as e:
                self.note_item_error("anatomy_figures", {"seed_key": f"anatomy-figure:{slug}"}, e)
                logger.error(f"Error seeding figure {slug}: {e}")
                local["errors"] += 1
        await session.flush()
        return local
