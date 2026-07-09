import logging
from typing import Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.anatomy_model import AnatomyStructure
from app.models.concept_model import ConceptEdge
from app.models.enums import (
    ConceptKind,
    ConceptRelationType,
    EdgeApprovalStatus,
    EdgeEndpointType,
)
from app.schemas.anatomy_import import AnatomyImportPayload
from app.services.concept_service import resolve_concept_by_slug

logger = logging.getLogger(__name__)


class AnatomyImportService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def import_graph(self, payload: AnatomyImportPayload) -> Dict[str, int]:
        stats = {
            "nodes_added": 0,
            "nodes_updated": 0,
            "edges_added": 0,
            "edges_updated": 0,
            "errors": 0,
        }

        node_id_map = {}

        # 1. Process Nodes
        for node_data in payload.nodes:
            try:
                result = await self.db.execute(
                    select(AnatomyStructure).where(
                        AnatomyStructure.slug == node_data.slug
                    )
                )
                existing_node = result.scalar_one_or_none()

                # Resolve the anatomy-class concept slug (e.g. ``organ``) to a
                # ``class_concept_id``.
                class_concept_id = None
                if getattr(node_data, "class_concept_slug", None):
                    class_concept_id = await resolve_concept_by_slug(
                        self.db,
                        node_data.class_concept_slug,
                        ConceptKind.ANATOMY_CLASS,
                    )

                if existing_node:
                    existing_node.name = node_data.name
                    existing_node.class_concept_id = class_concept_id
                    existing_node.standard_system = node_data.standard_system
                    existing_node.standard_code = node_data.standard_code
                    existing_node.description = node_data.description
                    existing_node.is_custom = node_data.is_custom
                    existing_node.display = node_data.display
                    stats["nodes_updated"] += 1
                    node_id_map[existing_node.slug] = existing_node.id
                else:
                    new_node = AnatomyStructure(
                        slug=node_data.slug,
                        name=node_data.name,
                        class_concept_id=class_concept_id,
                        standard_system=node_data.standard_system,
                        standard_code=node_data.standard_code,
                        description=node_data.description,
                        is_custom=node_data.is_custom,
                        display=node_data.display,
                    )
                    self.db.add(new_node)
                    await self.db.flush()
                    stats["nodes_added"] += 1
                    node_id_map[new_node.slug] = new_node.id
            except Exception as e:
                logger.error(f"Error processing anatomy node {node_data.slug}: {e}")
                stats["errors"] += 1

        # Refresh map for all nodes to allow edges to resolve even if node wasn't in payload
        all_nodes_result = await self.db.execute(
            select(AnatomyStructure.slug, AnatomyStructure.id)
        )
        for slug, node_id in all_nodes_result.all():
            node_id_map[slug] = node_id

        # 2. Process Edges — insert into the unified concept_edges table.
        for edge_data in payload.edges:
            try:
                source_id = node_id_map.get(edge_data.source_slug)
                target_id = node_id_map.get(edge_data.target_slug)

                if not source_id or not target_id:
                    logger.warning(
                        f"Skipping edge: Could not resolve source '{edge_data.source_slug}' or target '{edge_data.target_slug}'"
                    )
                    stats["errors"] += 1
                    continue

                rel_type = ConceptRelationType(edge_data.relation_type.value)

                # Check for existing edge (dedup).
                existing_edge_result = await self.db.execute(
                    select(ConceptEdge).where(
                        ConceptEdge.src_type == EdgeEndpointType.ANATOMY,
                        ConceptEdge.src_id == source_id,
                        ConceptEdge.dst_type == EdgeEndpointType.ANATOMY,
                        ConceptEdge.dst_id == target_id,
                        ConceptEdge.relation == rel_type,
                    )
                )
                existing_edge = existing_edge_result.scalar_one_or_none()

                if existing_edge:
                    stats["edges_updated"] += 1
                else:
                    new_edge = ConceptEdge(
                        src_type=EdgeEndpointType.ANATOMY,
                        src_id=source_id,
                        dst_type=EdgeEndpointType.ANATOMY,
                        dst_id=target_id,
                        relation=rel_type,
                        status=EdgeApprovalStatus.APPROVED,
                    )
                    self.db.add(new_edge)
                    stats["edges_added"] += 1
            except Exception as e:
                logger.error(
                    f"Error processing anatomy edge {edge_data.source_slug} -> {edge_data.target_slug}: {e}"
                )
                stats["errors"] += 1

        await self.db.commit()
        return stats
