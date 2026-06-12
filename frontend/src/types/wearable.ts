export interface WearableDataItem {
  id: string;
  tenant_id: string;
  device_id: string;
  timestamp: string;
  data: Record<string, unknown>;
  heart_rate?: number;
  steps?: number;
  calories?: number;
}

export interface WearableSummary {
  date?: string;
  steps: number;
  calories: number;
  heart_rate: {
    min: number;
    max: number;
    avg: number;
  };
}
