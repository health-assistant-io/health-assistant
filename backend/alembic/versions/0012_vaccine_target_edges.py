"""Vaccine target diseases → PREVENTS concept edges (ADR 0055 D8 / plan A Phase 3).

``vaccine_catalog.target_diseases`` stored disease-concept slugs as a JSONB
list — a slug-join duplicate of the knowledge graph: renaming a disease
concept silently orphaned the link, and the same facts lived in two places.
This revision materializes the lists as approved ``concept_edges`` rows
(``src_type=immunization → dst_type=concept``, ``relation=PREVENTS``,
``source=seed``) and drops the column — the graph is the single source of
truth for vaccine → disease links.

Upgrade: for every vaccine row carrying a ``target_diseases`` list, each slug
resolves against the SYSTEM concepts (``tenant_id IS NULL``, not deleted) and
the edge is inserted with a deterministic uuid5 id — the seeder-canonical
``vaccine-prevents:<vaccine-key>/<disease-slug>`` when the row carries a
``seed_key`` (a later reseed converges to exactly this edge), a row-based id
otherwise — unless an identical approved tuple already exists (the Phase-2
approved-edge unique index backs the guard). The edge inherits the vaccine
row's tenancy (system vaccine → system edge, tenant overlay → tenant edge); a
slug that cannot resolve is skipped with a logged warning — the column drops
regardless so no half-migrated shape remains, and the curated seed data
resolves fully (pinned by the migration test).

Downgrade: re-adds the JSONB column and backfills it from the approved
PREVENTS edges of each vaccine row — one sorted, deduplicated slug list per
row, deterministic order.

Revision ID: 0012_vaccine_target_edges
Revises: 0011_instance_catalog_fks
Create Date: 2026-11-09

"""

import json
import logging
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op
from app.core.catalog_identity import SEED_KEY_NAMESPACE

log = logging.getLogger("alembic.0012_vaccine_target_edges")

# revision identifiers, used by Alembic.
revision: str = "0012_vaccine_target_edges"
down_revision: str | Sequence[str] | None = "0011_instance_catalog_fks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Materialize target_diseases as approved seed PREVENTS edges, drop the column."""
    conn = op.get_bind()
    vaccines = (
        conn.execute(
            sa.text(
                "SELECT id, seed_key, slug, tenant_id, target_diseases "
                "FROM vaccine_catalog WHERE target_diseases IS NOT NULL"
            )
        )
        .mappings()
        .all()
    )

    created = 0
    unresolved = 0
    for vaccine in vaccines:
        label = vaccine["seed_key"] or vaccine["slug"]
        slugs = vaccine["target_diseases"]
        if isinstance(slugs, str):  # a driver that hands back the raw text
            slugs = json.loads(slugs)
        if not isinstance(slugs, list):
            continue  # a JSON ``null`` payload (not SQL NULL) — nothing to materialize
        for disease_slug in dict.fromkeys(slugs):
            concept_id = conn.execute(
                sa.text(
                    "SELECT id FROM concepts WHERE slug = :slug "
                    "AND tenant_id IS NULL AND deleted_at IS NULL"
                ),
                {"slug": disease_slug},
            ).scalar()
            if concept_id is None:
                unresolved += 1
                log.warning(
                    "vaccine %s: target disease slug %r resolves to no system "
                    "concept — edge skipped, the column drops regardless",
                    label,
                    disease_slug,
                )
                continue
            existing = conn.execute(
                sa.text(
                    "SELECT 1 FROM concept_edges WHERE tenant_id IS NOT DISTINCT FROM :tenant "
                    "AND src_type = 'immunization' AND src_id = :src "
                    "AND dst_type = 'concept' AND dst_id = :dst "
                    "AND relation = 'PREVENTS' AND status = 'approved' LIMIT 1"
                ),
                {"src": vaccine["id"], "dst": concept_id, "tenant": vaccine["tenant_id"]},
            ).first()
            if existing is not None:
                continue
            vaccine_key = (
                vaccine["seed_key"].split(":", 1)[1]
                if vaccine["seed_key"] and vaccine["seed_key"].startswith("vaccine:")
                else None
            )
            if vaccine_key is not None:
                # The seeder-canonical id: re-running the seed pipeline later
                # converges to exactly this edge (no id churn on reseed).
                edge_id = uuid.uuid5(
                    SEED_KEY_NAMESPACE, f"vaccine-prevents:{vaccine_key}/{disease_slug}"
                )
            else:
                # A row outside the curated identity keeps the row-based id.
                edge_id = uuid.uuid5(
                    SEED_KEY_NAMESPACE, f"vaccine-prevents-row:{vaccine['id']}/{concept_id}"
                )
            conn.execute(
                sa.text(
                    "INSERT INTO concept_edges "
                    "(id, src_type, src_id, dst_type, dst_id, relation, tenant_id, "
                    "source, status) VALUES "
                    "(:id, 'immunization', :src, 'concept', :dst, 'PREVENTS', "
                    ":tenant, 'seed', 'approved')"
                ),
                {
                    "id": str(edge_id),
                    "src": vaccine["id"],
                    "dst": concept_id,
                    "tenant": vaccine["tenant_id"],
                },
            )
            created += 1
    log.info(
        "0012: created %d PREVENTS edge(s); %d unresolved disease slug(s) skipped",
        created,
        unresolved,
    )
    op.drop_column("vaccine_catalog", "target_diseases")


def downgrade() -> None:
    """Re-add the JSONB column, backfilled from the approved PREVENTS edges."""
    op.add_column("vaccine_catalog", sa.Column("target_diseases", JSONB(), nullable=True))
    op.execute(
        "UPDATE vaccine_catalog v SET target_diseases = backfill.slugs FROM ("
        "  SELECT e.src_id AS vaccine_id, jsonb_agg(DISTINCT c.slug ORDER BY c.slug) AS slugs"
        "  FROM concept_edges e"
        "  JOIN concepts c ON c.id = e.dst_id"
        "  WHERE e.src_type = 'immunization' AND e.dst_type = 'concept'"
        "    AND e.relation = 'PREVENTS' AND e.status = 'approved'"
        "  GROUP BY e.src_id"
        ") AS backfill WHERE v.id = backfill.vaccine_id"
    )
