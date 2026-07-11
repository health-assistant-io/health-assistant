export type ConceptKind =
  | 'specialty'
  | 'examination_category'
  | 'event_category'
  | 'biomarker_class'
  | 'biomarker_panel'
  | 'anatomy_class'
  | 'vaccine_class'
  | 'medication_class'
  | 'document_category'
  | 'disease'
  | 'body_system'
  | 'procedure'
  | 'lifestyle'
  | 'factor'
  | 'symptom'
  | 'organ';

export type ConceptStatus = 'draft' | 'active' | 'retired';

export type ConceptProvenance = 'seed' | 'integration' | 'ai' | 'manual';

export type EdgeApprovalStatus = 'approved' | 'proposed' | 'rejected';

export type EdgeEndpointType =
  | 'concept'
  | 'biomarker'
  | 'medication'
  | 'clinical_event_type'
  | 'allergy'
  | 'immunization'
  | 'observation'
  | 'doctor'
  | 'examination'
  | 'anatomy'
  | 'document';

export type ConceptRelationType =
  | 'MEMBER_OF'
  | 'HAS_SPECIALTY'
  | 'CLASSIFIED_AS'
  | 'EXAMINES'
  | 'PERFORMS'
  | 'ORDERS'
  | 'LOCATED_IN'
  | 'PART_OF'
  | 'TREATS'
  | 'INDICATES'
  | 'PREVENTS'
  | 'CONTRAINDICATES'
  | 'CORRELATES_WITH'
  | 'CAUSED_BY'
  | 'MONITORS'
  | 'RISK_OF'
  | 'SCREENS_FOR';

export interface IconConfig {
  type: 'lucide' | 'custom_svg';
  value: string;
}

export interface Concept {
  id: string;
  slug: string;
  name: string;
  /** All domain tags on this concept (multi-kind). */
  kinds: ConceptKind[];
  /** Denormalized mirror of one tag, for single-badge rendering / coloring. */
  primary_kind?: ConceptKind | null;
  parent_id?: string | null;
  description?: string | null;
  coding_system?: string | null;
  code?: string | null;
  aliases?: string[];
  icon?: IconConfig | null;
  color?: string | null;
  status: ConceptStatus;
  display_order: number;
  meta_data?: Record<string, any> | null;
  tenant_id?: string | null;
  version?: number;
  created_at?: string | null;
  updated_at?: string | null;
}

/** True if a concept carries the given kind tag (multi-kind aware). */
export function hasKind(concept: Pick<Concept, 'kinds'>, kind: ConceptKind): boolean {
  return concept.kinds.includes(kind);
}

export interface ConceptEdge {
  id: string;
  src_type: EdgeEndpointType;
  src_id: string;
  dst_type: EdgeEndpointType;
  dst_id: string;
  relation: ConceptRelationType;
  properties?: Record<string, any> | null;
  evidence?: Record<string, any> | null;
  source: ConceptProvenance;
  status: EdgeApprovalStatus;
  tenant_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

/** A polymorphic edge endpoint resolved for display. The entity lives in its
 *  own table (single source of truth); this is a display reference. */
export interface ResolvedEndpoint {
  type: EdgeEndpointType;
  id: string;
  label: string;
  icon?: IconConfig | null;
  color?: string | null;
  kind?: string | null;
}

export interface NeighborResult {
  edge: ConceptEdge;
  direction: 'outgoing' | 'incoming';
  endpoint: ResolvedEndpoint | null;
}

export interface ConceptCreateInput {
  slug: string;
  name: string;
  /** One or more domain tags. At least one is required. */
  kinds: ConceptKind[];
  parent_id?: string;
  description?: string;
  coding_system?: string;
  code?: string;
  aliases?: string[];
  icon?: IconConfig;
  color?: string;
  display_order?: number;
  meta_data?: Record<string, any>;
  tenant_scoped?: boolean;
}

export interface ConceptUpdateInput {
  name?: string;
  parent_id?: string | null;
  description?: string;
  coding_system?: string;
  code?: string;
  aliases?: string[];
  icon?: IconConfig;
  color?: string;
  status?: ConceptStatus;
  display_order?: number;
  meta_data?: Record<string, any>;
  /** Replace the full set of kind tags (at least one required). */
  kinds?: ConceptKind[];
  primary_kind?: ConceptKind | null;
}

export const CONCEPT_KIND_LABELS: Record<ConceptKind, string> = {
  specialty: 'Doctor Specialties',
  examination_category: 'Examination Categories',
  event_category: 'Clinical Event Categories',
  biomarker_class: 'Biomarker Classes',
  biomarker_panel: 'Biomarker Panels',
  anatomy_class: 'Anatomy Classes',
  vaccine_class: 'Vaccine Classes',
  medication_class: 'Medication Classes (ATC)',
  document_category: 'Document Categories',
  disease: 'Diseases',
  body_system: 'Body Systems',
  procedure: 'Procedures',
  lifestyle: 'Lifestyle Factors',
  factor: 'Risk Factors',
  symptom: 'Symptoms',
  organ: 'Organs',
};

/** Tailwind-safe color per ConceptKind (for chip badges, graph nodes, etc.). */
export const KIND_COLORS: Record<ConceptKind, string> = {
  specialty: '#6366f1',
  examination_category: '#3b82f6',
  event_category: '#8b5cf6',
  biomarker_class: '#06b6d4',
  biomarker_panel: '#0ea5e9',
  anatomy_class: '#10b981',
  vaccine_class: '#84cc16',
  medication_class: '#f59e0b',
  document_category: '#64748b',
  disease: '#ef4444',
  body_system: '#ec4899',
  procedure: '#14b8a6',
  lifestyle: '#f97316',
  factor: '#eab308',
  symptom: '#f43f5e',
  organ: '#22c55e',
};

/** Colors for non-concept catalog types (biomarker, medication, etc.) used
 *  as fallbacks when the resolver doesn't populate ``color`` from
 *  ``class_concept``. */
export const CATALOG_TYPE_COLORS: Record<string, string> = {
  concept: '#6366f1',
  biomarker: '#06b6d4',
  medication: '#f59e0b',
  allergy: '#eab308',
  anatomy: '#10b981',
  vaccine: '#84cc16',
};

/** Human-readable labels for catalog types (used in graph filter chips). */
export const CATALOG_TYPE_LABELS: Record<string, string> = {
  concept: 'Concepts',
  biomarker: 'Biomarkers',
  medication: 'Medications',
  allergy: 'Allergies',
  anatomy: 'Anatomy',
  vaccine: 'Vaccines',
};

/** Lucide icon name per catalog type (mirrors the backend
 *  ``registrations.py`` UI metadata — Activity / Pill / ShieldAlert /
 *  PersonStanding / Syringe / Network). Used by graph filter chips + the
 *  node detail header for quick visual identification. */
export const CATALOG_TYPE_ICONS: Record<string, string> = {
  concept: 'Network',
  biomarker: 'Activity',
  medication: 'Pill',
  allergy: 'ShieldAlert',
  anatomy: 'PersonStanding',
  vaccine: 'Syringe',
};
