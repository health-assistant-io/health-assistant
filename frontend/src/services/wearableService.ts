import api from '../api/axios';
import { WearableDataItem, WearableSummary } from '../types/wearable';

export async function uploadWearableData(
  deviceId: string,
  data: Partial<WearableDataItem>[]
): Promise<{ uploaded: number; device_id: string }> {
  const response = await api.post<{ uploaded: number; device_id: string }>(
    '/wearable/data',
    { device_id: deviceId, data }
  );
  return response.data;
}

export async function getWearableData(
  deviceId: string,
  startDate: string,
  endDate: string,
  metrics?: string
): Promise<WearableDataItem[]> {
  const response = await api.get<WearableDataItem[]>(`/wearable/data`, {
    params: {
      device_id: deviceId,
      start_date: startDate,
      end_date: endDate,
      metrics
    }
  });
  return response.data;
}

export async function getWearableSummary(
  date: string,
  deviceId?: string
): Promise<WearableSummary> {
  const response = await api.get<WearableSummary>(
    '/wearable/data/summary',
    {
      params: {
        date,
        device_id: deviceId
      }
    }
  );
  return response.data;
}
