import axios from 'axios';
import api from '../api/axios';

export async function uploadDocument(
  file: File,
  patientId?: string,
  examinationId?: string,
  includeInExtraction: boolean = false
): Promise<{
  id: string;
  filename: string;
  file_path: string;
  status: string;
  progress?: number;
  error_message?: string;
  created_at: string;
  examination_id?: string;
}> {
  const formData = new FormData();
  formData.append('file', file);
  if (patientId) {
    formData.append('patient_id', patientId);
  }
  if (examinationId) {
    formData.append('examination_id', examinationId);
  }
  formData.append('include_in_extraction', includeInExtraction ? 'true' : 'false');

  const response = await api.post<{
    id: string;
    filename: string;
    file_path: string;
    status: string;
    progress?: number;
    error_message?: string;
    created_at: string;
    examination_id?: string;
  }>('/documents', formData, {
    headers: {
      'Content-Type': 'multipart/form-data'
    }
  });
  return response.data;
}

export async function getDocuments(): Promise<Array<{
  id: string;
  filename: string;
  file_path: string;
  status: string;
  progress?: number;
  error_message?: string;
  created_at: string;
  examination_id?: string;
  patient_id?: string;
}>> {
  const response = await api.get<Array<{
    id: string;
    filename: string;
    file_path: string;
    status: string;
    progress?: number;
    error_message?: string;
    created_at: string;
    examination_id?: string;
    patient_id?: string;
  }>>('/documents');
  return response.data;
}

export async function getDocument(documentId: string): Promise<{
  id: string;
  filename: string;
  file_path: string;
  status: string;
  progress?: number;
  error_message?: string;
  extracted_text?: string;
  entities?: any;
  created_at: string;
  examination_id?: string;
  patient_id?: string;
  owner_id?: string;
  file_size?: number;
  owner_email?: string;
  updated_at?: string;
  include_in_extraction?: boolean;
}> {
  const response = await api.get<{
    id: string;
    filename: string;
    file_path: string;
    status: string;
    progress?: number;
    error_message?: string;
    extracted_text?: string;
    entities?: any;
    created_at: string;
    examination_id?: string;
    patient_id?: string;
    owner_id?: string;
    file_size?: number;
    owner_email?: string;
    updated_at?: string;
    include_in_extraction?: boolean;
  }>(`/documents/${documentId}`);
  return response.data;
}

export async function updateDocument(documentId: string, updates: any): Promise<any> {
  const response = await api.patch(`/documents/${documentId}`, updates);
  return response.data;
}

export async function downloadDocument(documentId: string): Promise<Blob> {
  const presignResponse = await api.get<{ url: string }>(`/documents/${documentId}/presign`);
  // The backend returns a URL starting with /api/v1. 
  // We strip it here because the axios api instance already has /api/v1 in its baseURL.
  const relativeUrl = presignResponse.data.url.replace(/^\/api\/v1/, '');
  const response = await api.get(relativeUrl, {
    responseType: 'blob'
  });
  return response.data;
}

export async function getDocumentDownloadUrl(documentId: string): Promise<string> {
  // Construct the URL to the download endpoint using a short-lived presigned token
  const response = await api.get<{ url: string }>(`/documents/${documentId}/presign`);
  const baseUrl = import.meta.env.VITE_API_URL || '';
  // Strip /api/v1 if it's already in the baseUrl to avoid duplication, or just use the window origin if relative
  if (response.data.url.startsWith('http')) {
      return response.data.url;
  }
  return `${baseUrl.replace('/api/v1', '')}${response.data.url}`;
}

export async function getDocumentPreviewUrl(documentId: string, page: number = 0): Promise<{ url: string }> {
  // Use the same presigned token logic but for the preview endpoint
  const response = await api.get<{ url: string }>(`/documents/${documentId}/presign`);
  
  // Return the relative URL so it goes through the Vite proxy
  // This avoids CORS issues with 'fetch' in the browser
  const url = response.data.url.replace('/download?', `/preview?page=${page}&`);
  
  return { url };
}

export async function getTempPreviewUrl(file: File, page: number = 0): Promise<{ url: string; totalPages: number }> {
  const formData = new FormData();
  formData.append('file', file);
  
  const token = localStorage.getItem('accessToken');
  const baseUrl = import.meta.env.VITE_API_URL || '/api/v1';

  // Use a fresh axios call to bypass the default JSON content-type that interferes with FormData
  const response = await axios.post(`${baseUrl}/documents/preview-temp?page=${page}`, formData, {
    responseType: 'blob',
    headers: {
      'Authorization': `Bearer ${token}`
    }
  });
  
  const totalPages = parseInt(response.headers['x-total-pages'] || '1');
  
  return {
    url: URL.createObjectURL(response.data),
    totalPages
  };
}

export async function triggerExtraction(documentId: string): Promise<{ job_id: string }> {
  const response = await api.post<{ job_id: string }>(`/documents/${documentId}/extract`);
  return response.data;
}

export async function getExtractionStatus(documentId: string): Promise<{ status: string; progress: number; error_message?: string }> {
  const response = await api.get<{ status: string; progress: number; error_message?: string }>(
    `/documents/${documentId}/extract/status`
  );
  return response.data;
}

export async function getDicomMetadata(documentId: string): Promise<Record<string, { label: string; value: string }>> {
  const response = await api.get<Record<string, { label: string; value: string }>>(`/documents/${documentId}/dicom-metadata`);
  return response.data;
}

export async function deleteDocument(documentId: string): Promise<{ message: string }> {
  const response = await api.delete<{ message: string }>(`/documents/${documentId}`);
  return response.data;
}

export async function editDocument(documentId: string, params: {
  crop_left?: number;
  crop_top?: number;
  crop_right?: number;
  crop_bottom?: number;
  brightness?: number;
  contrast?: number;
  sharpness?: number;
}): Promise<any> {
  const response = await api.post(`/documents/${documentId}/edit`, params);
  return response.data;
}

export async function triggerDocumentDownload(documentId: string, filename: string): Promise<void> {
  try {
    const blob = await downloadDocument(documentId);
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
  } catch (error) {
    console.error('Download failed:', error);
    throw error;
  }
}
