import api from '../api/axios';
import { Patient } from '../types/patient';

export async function createPatient(patientData: Partial<Patient>, tenantId?: string): Promise<Patient> {
  const response = await api.post<Patient>('/patients', {
    ...patientData,
    tenant_id: tenantId
  });
  return response.data;
}

export async function getPatient(patientId: string): Promise<Patient> {
  const response = await api.get<Patient>(`/patients/${patientId}`);
  return response.data;
}

export async function updatePatient(patientId: string, patientData: Partial<Patient>): Promise<Patient> {
  const response = await api.put<Patient>(`/patients/${patientId}`, patientData);
  return response.data;
}

export async function deletePatient(patientId: string): Promise<{ message: string }> {
  const response = await api.delete<{ message: string }>(`/patients/${patientId}`);
  return response.data;
}

export async function updatePatientLayout(patientId: string, layout: any): Promise<Patient> {
  const response = await api.put<Patient>(`/patients/${patientId}/layout`, layout);
  return response.data;
}

export async function listPatients(
  tenantId?: string,
  limit: number = 10,
  offset: number = 0,
  userId?: string
): Promise<{ items: Patient[], total: number }> {
  const response = await api.get<{ items: Patient[], total: number }>(`/patients`, {
    params: { tenant_id: tenantId, limit, offset, user_id: userId }
  });
  return response.data;
}
