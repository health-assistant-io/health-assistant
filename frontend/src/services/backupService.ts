import api from '../api/axios';
import type {
  BackupRequest,
  ExportJob,
  ExportJobList,
  ImportJob,
} from '../types/backup';

export async function createExportJob(request: BackupRequest): Promise<ExportJob> {
  const response = await api.post<ExportJob>('/export', request);
  return response.data;
}

export async function listExportJobs(limit = 50): Promise<ExportJobList> {
  const response = await api.get<ExportJobList>('/export/jobs', {
    params: { limit },
  });
  return response.data;
}

export async function getExportJob(jobId: string): Promise<ExportJob> {
  const response = await api.get<ExportJob>(`/export/jobs/${jobId}`);
  return response.data;
}

export async function downloadExportFile(jobId: string, filename?: string): Promise<void> {
  const response = await api.get(`/export/jobs/${jobId}/download`, {
    responseType: 'blob',
  });
  const blob = response.data as Blob;
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return;
  }
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename || `export-${jobId}`;
  document.body.appendChild(a);
  a.click();
  window.URL.revokeObjectURL(url);
  document.body.removeChild(a);
}

export async function importBackupFile(file: File, autoMapBiomarkers = true, useAiNormalization = false): Promise<ImportJob> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('auto_map_biomarkers', String(autoMapBiomarkers));
  formData.append('use_ai_normalization', String(useAiNormalization));
  const response = await api.post<ImportJob>('/import/backup', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return response.data;
}

export async function getImportJob(jobId: string): Promise<ImportJob> {
  const response = await api.get<ImportJob>(`/import/jobs/${jobId}`);
  return response.data;
}
