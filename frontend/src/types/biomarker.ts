export type CodingSystemType = 'loinc' | 'snomed' | 'custom';

/** Discriminator — every Biomarker is either a numeric measurement
 * (``quantity`` — the legacy default) or a categorical state drawn from a
 * controlled vocabulary (``state`` — Positive/Negative/Detected/…). See
 * docs/STATE_BIOMARKERS.md. Drives branch points in ``useBiomarkers``,
 * the dashboard cards, and ``BiomarkerForm``. */
export type BiomarkerValueType = 'quantity' | 'state';

/** A row from the universal ``biomarker_states`` catalog
 * (HL7 v3-ObservationInterpretation + SNOMED + DataAbsentReason + a small
 * custom-urn namespace). Universally unique on ``(code, system)``. */
export interface BiomarkerState {
  id: string;
  slug: string;
  code: string;
  system: string;
  display: string;
  description?: string | null;
  category?: string | null;
  sort_order?: number;
}

/** A STATE biomarker's resolved allowed-state entry (join row + state).
 * The ``is_normal`` flag is the categorical equivalent of a numeric
 * reference range — the analytics status computation is
 * "state in normal_set → Normal else Abnormal". */
export interface AllowedState {
  state_id: string;
  state_slug: string;
  code: string;
  system: string;
  display: string;
  is_normal: boolean;
  sort_order: number;
}

/** Input shape when declaring an allowed state on a create/update payload.
 * The slug is the stable round-trip key (resolves to a BiomarkerState row
 * on the backend). */
export interface AllowedStateSpec {
  state_slug: string;
  is_normal?: boolean;
  sort_order?: number;
}

/** A reference range scoped to a sub-population (audit B9/F3).
 * A null/undefined dimension means "any value" for that axis (sex → both,
 * age_* → unbounded on that side, unit_id → any unit). The backend resolver
 * picks the most-specific applicable row for a patient. */
export interface BiomarkerReferenceRange {
  id?: string;
  biomarker_id?: string;
  sex?: 'MALE' | 'FEMALE' | 'OTHER' | 'UNKNOWN' | null;
  age_min?: number | null;
  age_max?: number | null;
  unit_id?: string | null;
  low?: number | null;
  high?: number | null;
  text?: string | null;
  applies_to?: string | null;
}

export enum DataSourceType {
  TELEMETRY = 'telemetry',
  EXAMINATION = 'examination',
  DOCUMENT = 'document',
  INTEGRATION = 'integration',
  UNKNOWN = 'unknown'
}

export interface Biomarker {
  id: string;
  slug: string;
  coding_system?: CodingSystemType;
  code?: string;
  name: string;
  category?: string;
  aliases: string[];
  preferred_unit_id?: string;
  preferred_unit_symbol?: string;
  info?: string;
  is_telemetry?: boolean;
  reference_range_min?: number;
  reference_range_max?: number;
  reference_ranges?: BiomarkerReferenceRange[];
  /** Discriminator (plan state-biomarkers-2026-08-05). Default ``quantity``
   * — every pre-state-biomarker row stays numeric. */
  value_type?: BiomarkerValueType;
  /** STATE biomarkers only: when true the biomarker accepts Observations
   * with FHIR ``component[]`` (one valueCodeableConcept per sub-context,
   * e.g. one organism per row in a microbiology panel). */
  supports_multi_state?: boolean;
  /** STATE biomarkers only: the controlled vocabulary this biomarker
   * accepts, with ``is_normal`` flags marking the normal set. Empty for
   * QUANTITY biomarkers. */
  allowed_states?: AllowedState[];
  meta_data?: {
    migration_status?: 'in_progress' | 'completed' | 'failed';
    migration_progress?: number;
    migration_error?: string;
    [key: string]: any;
  } | null;
}

export interface Unit {
  id: string;
  symbol: string;
  name: string;
  quantity_type: string;
  conversion_multiplier: number;
}

export interface BiomarkerGroup {
  id: string;
  name: string;
  type?: string;
  members: Biomarker[];
}

export interface ObservationSource {
  documentId: string;
  filename: string;
  examinationId?: string;
  date: string;
  labName?: string;
}

export interface BiomarkerObservation {
  id: string;
  displayName: string;
  slug: string | null;
  method: string | null;

  /** Value shape — branch on ``valueType``.
   *
   * Pre-state-biomarker the type was the lying ``{ raw: number; normalized:
   * number | null }`` while runtime routinely stored strings (tolerant
   * fallback at useBiomarkers.ts:242-243 + the data-corruption site at
   * :379). Now explicitly union: numeric for QUANTITY, string for STATE
   * (the state code/display from valueCodeableConcept). ``state`` carries
   * the resolved state code (POS/NEG/…) for STATE biomarkers; null for
   * QUANTITY. */
  valueType?: BiomarkerValueType;
  value: {
    raw: number | string | null;
    normalized: number | string | null;
    /** STATE biomarkers only: the valueCodeableConcept coding[0].code (e.g.
     * "POS"). Use ``stateDisplay`` for the human label. */
    state?: string | null;
    /** STATE biomarkers only: the human-readable label (coding[0].display
     * or text). */
    stateDisplay?: string | null;
    /** STATE biomarkers only: the code system URL (e.g. v3-ObservationInterpretation). */
    stateSystem?: string | null;
  };

  unit: {
    rawSymbol: string;
    normalizedSymbol?: string;
  };


  referenceRange: {
    min: number | null;
    max: number | null;
    displayText: string;
    raw?: {
      min: number | null;
      max: number | null;
      displayText: string;
    };
    standard?: {
      min: number | null;
      max: number | null;
      displayText: string;
    };
  };
  
  relativeScore: number | null;
  interpretation: string;
  
  source: ObservationSource;
  definitionId: string | null;
  info: string | null | undefined;
  aliases?: string[];
  isTelemetry?: boolean;
  isUnmapped?: boolean;
  _rawJson?: any; 
}