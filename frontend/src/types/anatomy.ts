export type AnatomyCategory =
  | 'SYSTEM'
  | 'REGION'
  | 'ORGAN'
  | 'ORGAN_PART'
  | 'TISSUE'
  | 'CELL'
  | 'SUBSTANCE'
  | 'JOINT'
  | 'OTHER';

export type AnatomyRelationType =
  | 'PART_OF'
  | 'BRANCH_OF'
  | 'DRAINS_INTO'
  | 'ARTICULATES_WITH'
  | 'INNERVATED_BY'
  | 'SUPPLIED_BY'
  | 'CONTINUOUS_WITH';

export type CodingSystem = 'loinc' | 'snomed' | 'custom';

export interface AnatomyMapMarker {
  /** @deprecated The figure slug now encodes the view; retained for back-compat. */
  view?: 'front' | 'back';
  /** Normalized x (0–1) within the figure's viewBox. */
  nx: number;
  /** Normalized y (0–1) within the figure's viewBox. */
  ny: number;
  /** Normalized radius (0–1) relative to the figure's viewBox height. */
  nr: number;
}

/**
 * Per-figure marker positions, keyed by figure slug (e.g. ``man-front``,
 * ``woman-back``). Each marker is normalized 0–1 against that figure's own
 * viewBox, so positions are resolution- and figure-independent. Edited via the
 * PositionEditor.
 */
export type MarkerMap = Record<string, AnatomyMapMarker>;

export interface AnatomyDisplay {
  map?: {
    markers?: MarkerMap;
  };
}

/** A body figure view stored as a raster image (replaces the old SVG atlas). */
export interface AnatomyFigure {
  id: string;
  slug: string;
  label: string;
  /** Groups views of one figure: "man", "woman", or a custom key. */
  figure_key: string;
  /** Free-form view tag: "front", "back", "left", ... */
  view_key: string;
  /** Relative path to the image under UPLOAD_DIR. */
  image_path?: string | null;
  /** Original uncropped source image (for re-cropping). Null if none stored. */
  source_image_path?: string | null;
  /** Image pixel dimensions — markers resolve against these (normalized 0-1). */
  width?: number | null;
  height?: number | null;
  sort_order: number;
  is_active: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface AnatomyStructure {
  id: string;
  name: string;
  slug: string;
  category: AnatomyCategory;
  /** Anatomy-class concept (lowercase slug, e.g. ``organ``). Projected by the
   * backend alongside the legacy uppercase ``category``. */
  class_concept_id?: string | null;
  class_concept_slug?: string | null;
  class_concept_name?: string | null;
  standard_system?: CodingSystem | null;
  standard_code?: string | null;
  description?: string | null;
  is_custom: boolean;
  display?: AnatomyDisplay | null;
  tenant_id?: string | null;
}

export interface AnatomyRelation {
  id: string;
  source_id: string;
  target_id: string;
  relation_type: AnatomyRelationType;
}

export interface AnatomyGraphNode extends AnatomyStructure {
  outgoing_relations?: AnatomyRelation[];
  incoming_relations?: AnatomyRelation[];
}

export interface AnatomyRelatedResponse {
  outgoing: Array<{
    relation_type: AnatomyRelationType;
    structure: AnatomyStructure;
  }>;
  incoming: Array<{
    relation_type: AnatomyRelationType;
    structure: AnatomyStructure;
  }>;
}

export interface AnatomyGraphEdge {
  source_id: string;
  target_id: string;
  relation_type: AnatomyRelationType;
}

/** A graph node annotated with its hop distance from the root. */
export interface AnatomyGraphNodeItem extends AnatomyStructure {
  depth: number;
}

export interface AnatomyGraphResponse {
  root_id: string;
  nodes: AnatomyGraphNodeItem[];
  edges: AnatomyGraphEdge[];
}

export interface AnatomyImportNode {
  slug: string;
  name: string;
  category: AnatomyCategory;
  standard_system?: CodingSystem | null;
  standard_code?: string | null;
  description?: string | null;
  is_custom?: boolean;
}

export interface AnatomyImportEdge {
  source_slug: string;
  target_slug: string;
  relation_type: AnatomyRelationType;
}

export interface AnatomyImportPayload {
  nodes: AnatomyImportNode[];
  edges: AnatomyImportEdge[];
}

export interface AnatomyListResponse {
  items: AnatomyStructure[];
  total: number;
}

export const CATEGORY_LABELS: Record<AnatomyCategory, string> = {
  SYSTEM: 'System',
  REGION: 'Region',
  ORGAN: 'Organ',
  ORGAN_PART: 'Organ Part',
  TISSUE: 'Tissue',
  CELL: 'Cell',
  SUBSTANCE: 'Substance',
  JOINT: 'Joint',
  OTHER: 'Other',
};

export const CATEGORY_COLORS: Record<AnatomyCategory, string> = {
  SYSTEM: '#3b82f6',
  REGION: '#22c55e',
  ORGAN: '#ef4444',
  ORGAN_PART: '#f97316',
  TISSUE: '#a855f7',
  CELL: '#ec4899',
  SUBSTANCE: '#14b8a6',
  JOINT: '#eab308',
  OTHER: '#6b7280',
};

/**
 * Lowercase anatomy-class concept slug → color/label. The canonical form now
 * that the legacy uppercase ``AnatomyCategory`` enum is dropped on the backend
 * (items carry ``class_concept_slug``). Mirrors the uppercase map's palette.
 */
export const CLASS_COLORS: Record<string, string> = {
  system: '#3b82f6',
  region: '#22c55e',
  organ: '#ef4444',
  'organ-part': '#f97316',
  tissue: '#a855f7',
  cell: '#ec4899',
  substance: '#14b8a6',
  joint: '#eab308',
  other: '#6b7280',
  'other-anatomy': '#6b7280',
};

export const CLASS_LABELS: Record<string, string> = {
  system: 'System',
  region: 'Region',
  organ: 'Organ',
  'organ-part': 'Organ Part',
  tissue: 'Tissue',
  cell: 'Cell',
  substance: 'Substance',
  joint: 'Joint',
  other: 'Other',
  'other-anatomy': 'Other',
};

export const CLASS_COLOR = (slug?: string | null): string =>
  (slug && CLASS_COLORS[slug]) || '#6b7280';

export const RELATION_LABELS: Record<AnatomyRelationType, string> = {
  PART_OF: 'Part of',
  BRANCH_OF: 'Branch of',
  DRAINS_INTO: 'Drains into',
  ARTICULATES_WITH: 'Articulates with',
  INNERVATED_BY: 'Innervated by',
  SUPPLIED_BY: 'Supplied by',
  CONTINUOUS_WITH: 'Continuous with',
};
