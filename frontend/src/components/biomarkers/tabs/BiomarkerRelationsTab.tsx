/**
 * BiomarkerDetail "Relations" tab — reuses the catalog's polymorphic relations
 * graph (`CatalogRelationsGraph`) against `GET /catalogs/biomarker/{id}/relations`.
 *
 * This is the read-only exploration view (depth traversal + cards). Authoring
 * (create/delete edges) lives in the Catalog workspace's Relations tab + the
 * inline `CatalogRelationsEditor` in the edit modal.
 */
import React from 'react';
import { CatalogRelationsGraph } from '../../catalog/CatalogRelationsGraph';
import type { Biomarker } from '../../../types/biomarker';

interface BiomarkerRelationsTabProps {
  biomarker: Biomarker;
}

export const BiomarkerRelationsTab: React.FC<BiomarkerRelationsTabProps> = ({ biomarker }) => {
  return (
    <div className="p-4 sm:p-6 animate-in fade-in duration-300 h-[500px]">
      <CatalogRelationsGraph
        catalogType="biomarker"
        itemId={biomarker.id}
        itemLabel={biomarker.name}
        showMiniMap={false}
      />
    </div>
  );
};
