import api from '../api/axios';

export async function createAlert(
  alertType: string,
  patientId: string,
  threshold?: number,
  enabled: boolean = true
): Promise<Alert> {
  const response = await api.post<Alert>('/alerts', {
    type: alertType,
    patient_id: patientId,
    threshold,
    enabled
  });
  return response.data;
}

export async function getAlert(alertId: string): Promise<Alert> {
  const response = await api.get<Alert>(`/alerts/${alertId}`);
  return response.data;
}

export async function listAlerts(
  patientId?: string,
  type?: string,
  status?: string,
  limit: number = 50,
  offset: number = 0
): Promise<{ items: Alert[]; total: number }> {
  const response = await api.get<{ items: Alert[]; total: number }>(`/alerts`, {
    params: {
      patient_id: patientId,
      type,
      status,
      limit,
      offset
    }
  });
  return response.data;
}

export async function updateAlert(
  alertId: string,
  threshold?: number,
  enabled?: boolean
): Promise<Alert> {
  const response = await api.put<Alert>(`/alerts/${alertId}`, {
    threshold,
    enabled
  });
  return response.data;
}

export async function deleteAlert(alertId: string): Promise<{ message: string }> {
  const response = await api.delete<{ message: string }>(`/alerts/${alertId}`);
  return response.data;
}

export async function triggerAlert(alertId: string): Promise<Alert> {
  const response = await api.post<Alert>(`/alerts/${alertId}/trigger`);
  return response.data;
}

export async function getAlertHistory(
  patientId?: string,
  startDate?: string,
  endDate?: string
): Promise<Alert[]> {
  const response = await api.get<Alert[]>(`/alerts/history`, {
    params: {
      patient_id: patientId,
      start_date: startDate,
      end_date: endDate
    }
  });
  return response.data;
}

interface Alert {
  id: string;
  type: string;
  patient_id: string;
  threshold?: number;
  enabled: boolean;
  created_at: string;
  last_triggered?: string;
}