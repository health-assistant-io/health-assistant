export type CodingSystemType = 'loinc' | 'snomed' | 'custom';

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
  reference_range_min?: number;
  reference_range_max?: number;
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
  _rawJson?: any; 
}