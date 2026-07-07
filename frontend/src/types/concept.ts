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
