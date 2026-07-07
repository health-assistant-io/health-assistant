import api from '../api/axios';
import { db } from './db';
import { getAIAssistance } from './aiAssistanceService';

export async function createExamination(data: {
  patient_id?: string;
  examination_date?: string;
  notes?: string;
  patient_notes?: string;
  category?: string;
  doctor_ids?: string[];
  organization_id?: string;
  auto_extract_metadata?: boolean;
}) {
  const response = await api.post('/examinations', data);
  return response.data;
}

export async function magicFillExamination(userInput: string): Promise<any> {
  const res = await getAIAssistance({
    task_type: 'magic_fill_examination',
    user_input: userInput
  });
  return res.suggested_data;
}

export async function getExaminationCategories(): Promise<any[]> {
  try {
    const response = await api.get('/concepts?kind=examination_category&limit=500');
    const categories = response.data;
    if (categories && categories.length > 0) {
      await db.metadata.put({
        key: 'examination_categories',
        value: categories,
        updatedAt: Date.now()
      });
    }
    return categories;
  } catch (error) {
    if (!navigator.onLine) {
      const cached = await db.metadata.get('examination_categories');
      if (cached) return cached.value;
    }
    throw error;
  }
}

export async function createExaminationCategory(data: {
  name: string;
  slug: string;
  description?: string;
  color?: string;
  icon?: any;
}) {
  const response = await api.post('/concepts', { ...data, kind: 'examination_category' });
  return response.data;
}

export async function updateExaminationCategory(id: string, data: {
  name?: string;
  slug?: string;
  description?: string;
  color?: string;
  icon?: any;
}) {
  const response = await api.put(`/concepts/${id}`, data);
  return response.data;
}

export async function deleteExaminationCategory(id: string) {
  const response = await api.delete(`/concepts/${id}`);
  return response.data;
}

export async function getCachedExaminations(patientId: string) {
  return await db.examinations.where('patient_id').equals(patientId).reverse().sortBy('examination_date');
}

export async function getExaminations(patientId?: string, limit: number = 50, offset: number = 0) {
  const params: any = { limit, offset };
  if (patientId) params.patient_id = patientId;
  
  try {
    const response = await api.get('/examinations', { params });
    const exams = response.data;
    
    // Proactively cache basic exam info without wiping out detailed clinical data if already present
    if (exams && Array.isArray(exams)) {
      for (const exam of exams) {
        const existing = await db.examinations.get(exam.id);
        if (existing) {
          // Merge: Keep existing observations/medications if the new one doesn't have them (summary)
          await db.examinations.put({
            ...existing,
            ...exam,
            updatedAt: Date.now()
          });
        } else {
          await db.examinations.put({
            ...exam,
            updatedAt: Date.now()
          });
        }
      }
    }
    return exams;
  } catch (error) {
    if (!navigator.onLine && patientId) {
      console.log('Fetching examinations from offline cache...');
      return await db.examinations.where('patient_id').equals(patientId).reverse().sortBy('examination_date');
    }
    throw error;
  }
}

export async function getExamination(examinationId: string) {
  try {
    const response = await api.get(`/examinations/${examinationId}`);
    const exam = response.data;
    
    // Cache the full exam details
    db.examinations.put({
      ...exam,
      updatedAt: Date.now()
    });
    return exam;
  } catch (error) {
    if (!navigator.onLine) {
      console.log('Fetching single examination from offline cache...');
      const cached = await db.examinations.get(examinationId);
      if (cached) return cached;
    }
    throw error;
  }
}

export async function getExaminationStatus(examinationId: string) {
  const response = await api.get(`/examinations/${examinationId}/status`);
  return response.data;
}

export async function updateExamination(examinationId: string, data: { notes?: string; patient_notes?: string; category?: string; doctor_ids?: string[]; organization_id?: string | null; diagnoses?: string[]; impressions?: string; examination_date?: string; }) {
  const response = await api.put(`/examinations/${examinationId}`, data);
  
  // Update local cache if online request succeeded
  if (response.data && !response.data._offline) {
    db.examinations.update(examinationId, {
      ...response.data,
      updatedAt: Date.now()
    });
  }
  return response.data;
}

export async function getExaminationDocuments(examinationId: string) {
  try {
    const response = await api.get(`/examinations/${examinationId}/documents`);
    return response.data;
  } catch (error) {
    if (!navigator.onLine) {
      // For documents, we don't cache binary files in IndexedDB by default (bloat)
      // but we could at least show the metadata if we had a separate docs table.
      // For now, let's just allow it to fail gracefully or return empty.
      return [];
    }
    throw error;
  }
}

export async function deleteExamination(examinationId: string) {
  const response = await api.delete(`/examinations/${examinationId}`);
  if (!response.data?._offline) {
    await db.examinations.delete(examinationId);
  }
  return response.data;
}

export async function bulkDeleteExaminations(examinationIds: string[]) {
  const response = await api.post('/examinations/bulk-delete', { examination_ids: examinationIds });
  if (!response.data?._offline) {
    for (const id of examinationIds) {
      await db.examinations.delete(id);
    }
  }
  return response.data;
}

export async function extractExamination(examinationId: string, mode: 'full' | 'extract_only' = 'full') {
  const response = await api.post(`/examinations/${examinationId}/extract`, { mode });
  return response.data;
}

export async function getExaminationLogs(examinationId: string) {
  const response = await api.get(`/examinations/${examinationId}/logs`);
  return response.data;
}
