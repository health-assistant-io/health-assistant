import { Observation } from './observation';
import { MedicationRecord } from '../services/medicationService';

export interface ContactPoint {
  system: 'phone' | 'email' | 'fax' | 'url' | 'pager' | 'sms' | 'other';
  value: string;
  use?: 'home' | 'work' | 'temp' | 'old' | 'mobile';
}

export interface Doctor {
  id: string;
  name: string;
  specialty?: string;
  license_number?: string;
  email?: string;
  phone?: string;
  user_id?: string;
  telecom?: ContactPoint[];
  address?: any;
  office_number?: string;
  office_details?: string;
}

export interface Organization {
  id: string;
  name: string;
  org_type: string;
  active: boolean;
  type?: any[];
  telecom?: any[];
  address?: any[];
  part_of_id?: string;
  departments?: Organization[];
  doctors?: Doctor[];
}

export interface Examination {
  id: string;
  patient_id: string;
  examination_date: string;
  notes?: string;
  patient_notes?: string;
  category_concept_id?: string;
  category?: string;
  category_concept?: any;
  organization_id?: string;
  organization?: Organization;
  doctors?: Doctor[];
  observations?: Observation[];
  medications?: MedicationRecord[];
  extraction_status?: string;
  extraction_progress?: number;
  error_message?: string;
  diagnoses?: any[];
  impressions?: string;
  created_at?: string;
  updated_at?: string;
  document_statuses?: any[];
}
