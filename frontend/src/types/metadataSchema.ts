/**
 * Typed descriptor for `ClinicalEventType.metadata_schema`.
 *
 * Mirrors the backend Pydantic models in
 * `backend/app/schemas/clinical_event.py` (`MetadataField` / `MetadataSchema`)
 * and the enums in `backend/app/models/enums.py`
 * (`MetadataFieldType` / `CatalogType` / `CatalogRelationType`). The wire
 * values are lowercase so the same string flows seed → JSONB → frontend
 * unchanged.
 *
 * `CatalogType` is re-exported from `./catalog` (its canonical home) — do not
 * redefine it here.
 */

import type { CatalogType } from './catalog';

/** The renderer discriminator — drives the exhaustive switch in
 *  `DynamicMetadataForm`. Adding a value here without a render branch is a
 *  compile error (the `default: never` guard). */
export type MetadataFieldType =
  | 'text'
  | 'number'
  | 'date'
  | 'boolean'
  | 'catalog-select';

/** How a picked catalog item in a `catalog-select` field relates to the
 *  event. Mirrors backend `CatalogRelationType`. */
export type CatalogRelationType =
  | 'primary_site'
  | 'radiates_to'
  | 'referred_to'
  | 'monitors'
  | 'treats'
  | 'indicates';

/**
 * ConceptKind values accepted by the `concept_kind` field. Mirrors the
 * backend `ConceptKind` enum — only the subset relevant to catalog-select
 * narrowing is listed here (the full enum has 16 values; only ones a form
 * author would realistically filter on are exposed as a union so typos are
 * caught at compile time). Use the string form for any value not listed.
 */
export type ConceptKindValue =
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

export interface MetadataField {
  name: string;
  label: string;
  type: MetadataFieldType;
  required?: boolean;
  /** Optional input placeholder for text/number fields (shown greyed when the
   *  field is empty). Helps the user understand the field's scope. */
  placeholder?: string;
  /** `catalog-select` only — which catalogs the picker may search. Required
   *  when `type === 'catalog-select'` (enforced server-side). */
  catalogs?: CatalogType[];
  /** Only valid when `catalogs === ['concept']`: narrows to one ConceptKind
   *  (e.g. `'examination_category'`). */
  concept_kind?: ConceptKindValue;
  /** `catalog-select` only — single vs multi selection. */
  multi?: boolean;
  /** Optional semantic hint for how the picked item relates to the event. */
  relation?: CatalogRelationType;
  /** `number` only — inclusive bounds. */
  min?: number;
  max?: number;
}

export interface MetadataSchema {
  fields: MetadataField[];
}

/**
 * The stored shape of a single-value `catalog-select` field's value in
 * `event_metadata`. Multi-value fields store an array of these.
 *
 * Re-exported `CatalogSelection` from `./catalog` is structurally identical
 * (`{type, id, label, relation?}`) — this alias documents intent for the
 * metadata-form author and the AI proposal prefill.
 */
export interface CatalogFieldValue {
  type: string;
  id: string;
  label: string;
  relation?: string;
}
