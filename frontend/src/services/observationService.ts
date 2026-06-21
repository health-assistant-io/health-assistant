import api from '../api/axios';
import { Observation } from '../types/observation';

export async function createObservation(observationData: Partial<Observation>, tenantId?: string): Promise<Observation> {
  const response = await api.post<Observation>('/observations', {
    ...observationData,
    tenant_id: tenantId
  });
  return response.data;
}

export async function getObservation(observationId: string): Promise<Observation> {
  const response = await api.get<Observation>(`/observations/${observationId}`);
  return response.data;
}

export async function deleteObservation(observationId: string): Promise<{ message: string }> {
  const response = await api.delete<{ message: string }>(`/observations/${observationId}`);
  return response.data;
}

export async function listObservations(
  tenantId?: string,
  patientId?: string,
  code?: string,
  startDate?: string,
  endDate?: string,
  limit: number = 100,
  offset: number = 0
): Promise<{ items: Observation[], total: number }> {
  const response = await api.get<{ items: Observation[], total: number }>(`/observations`, {
    params: {
      tenant_id: tenantId,
      patient_id: patientId,
      code,
      start_date: startDate,
      end_date: endDate,
      limit,
      offset
    }
  });
  return response.data;
}
