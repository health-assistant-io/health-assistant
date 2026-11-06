"""Body-part and anatomy-figure seeding."""

import contextlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.anatomy_model import AnatomyFigure, AnatomyStructure
from app.models.enums import CodingSystem, ConceptKind
from app.services.catalog.concept_service import resolve_concept_by_slug

logger = logging.getLogger(__name__)


class AnatomySeedMixin:
    async def seed_body_parts(self, session: AsyncSession = None) -> dict[str, int]:
        """
        Sync anatomy structures from JSON to Database.

        Reads ``anatomy_structures.json`` (nodes only). The anatomy hierarchy
        edges (PART_OF, SUPPLIED_BY, etc.) now live in ``concept_edges.json``
        and are seeded by :meth:`seed_concept_edges` — this method no longer
        loads ``anatomy_relations.json`` (that file is deleted; the table is
        dropped in migration ``h9c0d1e2f3a4``).
        """
        nodes = self._load_seed_json("anatomy_structures.json")
        if nodes is None:
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}

        anatomy_data = {
            "nodes": nodes.get("items", []) if isinstance(nodes, dict) else nodes,
            "edges": [],
        }

        if session:
            return await self._process_body_parts(session, anatomy_data)
        else:
            async with AsyncSessionLocal() as new_session:
                result = await self._process_body_parts(new_session, anatomy_data)
                await new_session.commit()
                return result

    async def _process_body_parts(
        self, session: AsyncSession, data: dict[str, Any]
    ) -> dict[str, int]:
        stats = {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        if not await self._table_exists(session, "anatomy_structures"):
            logger.error("Table 'anatomy_structures' does not exist. Skipping seeding.")
            return {
                "added": 0,
                "updated": 0,
                "skipped": len(data.get("nodes", [])),
                "errors": 0,
            }

        slug_to_id = {}
        nodes = data.get("nodes", [])

        for item in nodes:
            try:
                stmt = select(AnatomyStructure).where(AnatomyStructure.slug == item["slug"])
                result = await session.execute(stmt)
                db_part = result.scalar_one_or_none()

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

                if db_part:
                    db_part.name = item.get("name", db_part.name)
                    # Never nullify an existing class_concept_id on re-seed.
                    # If the concept currently can't be resolved (e.g. its
                    # kind tag is missing from a prior partial seed), keep the
                    # previously-resolved value rather than destroying it.
                    if class_concept_id is not None:
                        db_part.class_concept_id = class_concept_id
                    db_part.standard_system = standard_sys
                    db_part.standard_code = item.get("standard_code", db_part.standard_code)
                    db_part.description = item.get("description", db_part.description)
                    db_part.display = item.get("display", db_part.display)
                    stats["updated"] += 1
                    slug_to_id[db_part.slug] = db_part.id
                else:
                    new_part = AnatomyStructure(
                        id=uuid4(),
                        name=item["name"],
                        slug=item["slug"],
                        class_concept_id=class_concept_id,
                        standard_system=standard_sys,
                        standard_code=item.get("standard_code"),
                        description=item.get("description"),
                        display=item.get("display"),
                        is_custom=False,
                        tenant_id=None,
                        created_at=datetime.now(UTC),
                    )
                    session.add(new_part)
                    await session.flush()
                    stats["added"] += 1
                    slug_to_id[new_part.slug] = new_part.id
            except Exception as e:
                logger.error(f"Error seeding anatomy node {item.get('slug')}: {e}")
                stats["errors"] += 1

        all_nodes_result = await session.execute(select(AnatomyStructure.slug, AnatomyStructure.id))
        for slug, node_id in all_nodes_result.all():
            slug_to_id[slug] = node_id

        # Anatomy hierarchy edges (PART_OF, SUPPLIED_BY, etc.) are now seeded
        # by seed_concept_edges from concept_edges.json (src_type=anatomy,
        # dst_type=anatomy). The anatomy_relations.json + edge loop is
        # gone — the table was dropped in migration h9c0d1e2f3a4.

        return stats

    async def seed_anatomy_figures(self, session: AsyncSession = None) -> dict[str, int]:
        """
        Seed the four default body figures (man/woman x front/back) from WebP
        files under data/seeds/anatomy_figures/. Each file is copied into
        HA_UPLOAD_DIR/anatomy_figures/ and the DB row records its path + pixel
        dimensions. Wikimedia surface diagrams, CC BY-SA 3.0 (see NOTICE).

        Idempotent: inserts missing rows and refreshes the image file when the
        seed changes. Markers are normalized 0-1 against pixel dimensions.
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
        seeds_dir = self.seeds_dir / "anatomy_figures"
        from app.core.config import settings
        from app.services.anatomy.anatomy_service import FIGURES_DIR, _figures_base_dir

        async def _do(s: AsyncSession) -> dict[str, int]:
            local = {"added": 0, "updated": 0, "skipped": 0, "errors": 0}
            if not await self._table_exists(s, "anatomy_figures"):
                logger.error("Table 'anatomy_figures' does not exist. Skipping figure seeding.")
                local["errors"] = 1
                return local
            _figures_base_dir()  # ensure the upload dir exists (side effect)
            for slug, fname, label, fkey, vkey, order in spec:
                try:
                    src = seeds_dir / fname
                    if not src.exists():
                        logger.warning(f"Figure seed not found: {src}")
                        local["errors"] += 1
                        continue
                    data = src.read_bytes()
                    import io

                    from PIL import Image as PILImage

                    with PILImage.open(io.BytesIO(data)) as img:
                        w, h = img.size
                    rel_path = f"{FIGURES_DIR}/{fname}"
                    dest = Path(str(settings.HA_UPLOAD_DIR)) / rel_path
                    need_write = (not dest.exists()) or (dest.read_bytes() != data)
                    if need_write:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(data)
                    existing_res = await s.execute(
                        select(AnatomyFigure).where(AnatomyFigure.slug == slug)
                    )
                    existing = existing_res.scalar_one_or_none()
                    if existing:
                        if (
                            existing.image_path != rel_path
                            or existing.width != w
                            or existing.height != h
                        ):
                            existing.image_path = rel_path
                            existing.width, existing.height = w, h
                            existing.label = label
                            existing.sort_order = order
                            local["updated"] += 1
                        continue
                    s.add(
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
                    logger.error(f"Error seeding figure {slug}: {e}")
                    local["errors"] += 1
            await s.commit()
            return local

        if session:
            return await _do(session)
        async with AsyncSessionLocal() as new_session:
            return await _do(new_session)
