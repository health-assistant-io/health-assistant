export type CodingSystemType = 'loinc' | 'snomed' | 'custom';

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
  
  value: {
    raw: number;
    normalized: number | null;
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