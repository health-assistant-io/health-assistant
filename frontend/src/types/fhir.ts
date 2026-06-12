export interface Patient {
  id: string;
  name: {
    family: string;
    given: string[];
  };
  gender: 'male' | 'female' | 'other' | 'unknown';
  birthDate: string;
  mrn?: string;
  dashboard_layout?: any;
}

export interface Observation {
  id: string;
  status: string;
  code: {
    coding: Array<{
      system: string;
      code: string;
      display: string;
    }>;
  };
  valueQuantity?: {
    value: number;
    unit: string;
    system: string;
    code: string;
  };
  valueString?: string;
  effectiveDateTime: string;
  subject: {
    reference: string;
  };
}

export interface DiagnosticReport {
  id: string;
  status: string;
  category: Array<{
    coding: Array<{
      system: string;
      code: string;
      display: string;
    }>;
  }>;
  code: {
    coding: Array<{
      system: string;
      code: string;
      display: string;
    }>;
  };
  subject: {
    reference: string;
  };
  effectiveDateTime: string;
  issued: string;
  conclusion?: string;
}

export interface Medication {
  id: string;
  code: {
    coding: Array<{
      system: string;
      code: string;
      display: string;
    }>;
  };
  status: string;
  subject: {
    reference: string;
  };
  startDate?: string;
  endDate?: string;
}