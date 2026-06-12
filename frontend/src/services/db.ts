import Dexie, { Table } from 'dexie';

export interface PendingSync {
  id?: number;
  method: string;
  url: string;
  data: any;
  headers: any;
  timestamp: number;
  retries: number;
  status: 'pending' | 'syncing' | 'failed';
}

export interface LocalDraft {
  id: string; // e.g., 'examination-draft-123'
  type: 'examination' | 'note' | 'biomarker';
  data: any;
  updatedAt: number;
}

export interface CachedExamination {
  id: string;
  patient_id: string;
  examination_date: string;
  category: string;
  notes?: string;
  patient_notes?: string;
  extraction_status?: string;
  extraction_progress?: number;
  error_message?: string;
  doctors?: any[];
  observations?: any[];
  updatedAt: number;
}

export interface CachedBiomarkerTrend {
  id?: number;
  patient_id: string;
  period: string;
  data: any;
  updatedAt: number;
}

export interface CachedDashboardData {
  id?: number;
  patient_id: string;
  period: string;
  data: any;
  updatedAt: number;
}

export interface Metadata {
  key: string;
  value: any;
  updatedAt: number;
}

export class HealthAssistantDB extends Dexie {
  pendingSync!: Table<PendingSync>;
  localDrafts!: Table<LocalDraft>;
  examinations!: Table<CachedExamination>;
  biomarkerTrends!: Table<CachedBiomarkerTrend>;
  dashboardData!: Table<CachedDashboardData>;
  metadata!: Table<Metadata>;

  constructor() {
      super('health-assistant-offline');
    this.version(5).stores({
      pendingSync: '++id, method, url, status, timestamp',
      localDrafts: 'id, type, updatedAt',
      examinations: 'id, patient_id, examination_date, category',
      biomarkerTrends: '++id, [patient_id+period]',
      dashboardData: '++id, [patient_id+period]',
      metadata: 'key'
    });
  }
}

export const db = new HealthAssistantDB();
