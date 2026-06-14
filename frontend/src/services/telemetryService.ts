import api from '../api/axios';
import { TelemetryDataItem, TelemetrySummary } from '../types/telemetry';

export async function uploadTelemetryData(
  deviceId: string,
  data: Partial<TelemetryDataItem>[]
): Promise<{ uploaded: number; message: string }> {
  const payload = { device_id: deviceId, points: data };
  const response = await api.post(`/telemetry/data`, payload);
  return response.data;
}

export async function getTelemetryData(
  deviceId: string,
  startDate: string,
  endDate: string,
  metrics?: string
): Promise<TelemetryDataItem[]> {
  const response = await api.get<TelemetryDataItem[]>(`/telemetry/data`, {
    params: { device_id: deviceId, start_date: startDate, end_date: endDate, metrics },
  });
  return response.data;
}

export async function getTelemetrySummary(date: string, deviceId?: string): Promise<TelemetrySummary> {
  const response = await api.get<TelemetrySummary>(`/telemetry/data/summary`, {
    params: { date, device_id: deviceId },
  });
  return response.data;
}
