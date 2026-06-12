import api from '../api/axios';
import { Patient, Observation, DiagnosticReport, Medication } from '../types/fhir';

export async function createPatient(patientData: Partial<Patient>, tenantId?: string): Promise<Patient> {
  const response = await api.post<Patient>('/fhir/Patient', {
    ...patientData,
    tenant_id: tenantId
  });
  return response.data;
}

export async function getPatient(patientId: string): Promise<Patient> {
  const response = await api.get<Patient>(`/fhir/Patient/${patientId}`);
  return response.data;
}

export async function updatePatient(patientId: string, patientData: Partial<Patient>): Promise<Patient> {
  const response = await api.put<Patient>(`/fhir/Patient/${patientId}`, patientData);
  return response.data;
}

export async function deletePatient(patientId: string): Promise<{ message: string }> {
  const response = await api.delete<{ message: string }>(`/fhir/Patient/${patientId}`);
  return response.data;
}

export async function updatePatientLayout(patientId: string, layout: any): Promise<Patient> {
  const response = await api.put<Patient>(`/fhir/Patient/${patientId}/layout`, layout);
  return response.data;
}

export async function listPatients(
  tenantId?: string, 
  limit: number = 10, 
  offset: number = 0,
  userId?: string
): Promise<{ items: Patient[], total: number }> {
  const response = await api.get<{ items: Patient[], total: number }>(`/fhir/Patient`, {
    params: { tenant_id: tenantId, limit, offset, user_id: userId }
  });
  return response.data;
}

export async function createObservation(observationData: Partial<Observation>, tenantId?: string): Promise<Observation> {
  const response = await api.post<Observation>('/fhir/Observation', {
    ...observationData,
    tenant_id: tenantId
  });
  return response.data;
}

export async function getObservation(observationId: string): Promise<Observation> {
  const response = await api.get<Observation>(`/fhir/Observation/${observationId}`);
  return response.data;
}

export async function deleteObservation(observationId: string): Promise<{ message: string }> {
  const response = await api.delete<{ message: string }>(`/fhir/Observation/${observationId}`);
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
  const response = await api.get<{ items: Observation[], total: number }>(`/fhir/Observation`, {
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

export async function getObservationHistory(
  patientId: string,
  code: string,
  period: string = 'last-6-months'
): Promise<{ observations: Observation[], trend: string }> {
  const response = await api.get<{ observations: Observation[], trend: string }>(
    `/fhir/Observation/history`,
    {
      params: {
        patient_id: patientId,
        code,
        period
      }
    }
  );
  return response.data;
}

export async function createDiagnosticReport(
  reportData: Partial<DiagnosticReport>,
  tenantId?: string
): Promise<DiagnosticReport> {
  const response = await api.post<DiagnosticReport>('/fhir/DiagnosticReport', {
    ...reportData,
    tenant_id: tenantId
  });
  return response.data;
}

export async function getDiagnosticReport(reportId: string): Promise<DiagnosticReport> {
  const response = await api.get<DiagnosticReport>(`/fhir/DiagnosticReport/${reportId}`);
  return response.data;
}

export async function createMedication(
  medicationData: Partial<Medication>,
  tenantId?: string
): Promise<Medication> {
  const response = await api.post<Medication>('/fhir/Medication', {
    ...medicationData,
    tenant_id: tenantId
  });
  return response.data;
}

export async function getMedication(medicationId: string): Promise<Medication> {
  const response = await api.get<Medication>(`/fhir/Medication/${medicationId}`);
  return response.data;
}

export async function listMedications(
  tenantId?: string,
  patientId?: string,
  status?: string,
  limit: number = 100,
  offset: number = 0
): Promise<{ items: Medication[], total: number }> {
  const response = await api.get<{ items: Medication[], total: number }>(`/fhir/Medication`, {
    params: {
      tenant_id: tenantId,
      patient_id: patientId,
      status,
      limit,
      offset
    }
  });
  return response.data;
}
