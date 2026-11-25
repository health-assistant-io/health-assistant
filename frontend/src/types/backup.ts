export type ExportScope = 'patient' | 'group' | 'system';
export type ExportType = 'fhir_only' | 'full_backup' | 'catalog_only';
export type JobStatus = 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED' | 'PARTIAL';

export interface BackupRequest {
  scope: ExportScope;
  export_type: ExportType;
  patient_ids?: string[];
  include_documents?: boolean;
  include_telemetry?: boolean;
  include_integrations?: boolean;
  include_ai_config?: boolean;
}

export interface ExportJob {
  id: string;
  tenant_id?: string | null;
  user_id?: string | null;
  scope: ExportScope;
  export_type: ExportType;
  status: JobStatus;
  progress: number;
  patient_ids?: string[] | null;
  file_size_bytes?: number | null;
  resource_counts?: Record<string, number> | null;
  smart_scope?: string | null;
  error_message?: string | null;
  completed_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ExportJobList {
  items: ExportJob[];
  total: number;
}

export interface ImportJob {
  id: string;
  tenant_id?: string | null;
  user_id?: string | null;
  source_filename?: string | null;
  status: JobStatus;
  progress: number;
  total_records?: number | null;
  processed_records?: number | null;
  failed_records?: number | null;
  restore_result?: {
    created_resources?: Record<string, number>;
    updated_resources?: Record<string, number>;
    manifest_verified?: boolean;
    fhir_validated?: boolean;
  } | null;
  errors?: string[] | null;
  warnings?: string[] | null;
  error_message?: string | null;
  completed_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ManifestFile {
  path: string;
  sha256: string;
  size: number;
}

export interface BackupManifest {
  schema_version: string;
  exported_at: string;
  tenant_id?: string | null;
  fhir_version: string;
  scope: ExportScope;
  export_type: ExportType;
  smart_scope: string;
  source: string;
  counts: Record<string, number>;
  files: ManifestFile[];
  options: Record<string, boolean>;
  notes?: string[] | null;
}

export const EXPORT_TYPE_LABEL_KEYS: Record<ExportType, string> = {
  fhir_only: 'export_import.type_fhir_only',
  full_backup: 'export_import.type_full_backup',
  catalog_only: 'export_import.type_catalog_only',
};

export const EXPORT_SCOPE_LABEL_KEYS: Record<ExportScope, string> = {
  patient: 'export_import.scope_patient',
  group: 'export_import.scope_group',
  system: 'export_import.scope_system',
};

export const JOB_STATUS_LABEL_KEYS: Record<JobStatus, string> = {
  PENDING: 'export_import.status_pending',
  PROCESSING: 'export_import.status_processing',
  COMPLETED: 'export_import.status_completed',
  FAILED: 'export_import.status_failed',
  PARTIAL: 'export_import.status_partial',
};

export const JOB_STATUS_COLORS: Record<JobStatus, string> = {
  PENDING: 'bg-gray-100 text-gray-700 dark:bg-gray-700/30 dark:text-gray-300',
  PROCESSING: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
  COMPLETED: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300',
  FAILED: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300',
  PARTIAL: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
};

export const TERMINAL_STATUSES: JobStatus[] = ['COMPLETED', 'FAILED', 'PARTIAL'];
