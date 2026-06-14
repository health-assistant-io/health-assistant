export interface User {
  id: string;
  email: string;
  role: 'admin' | 'manager' | 'user';
  tenant_id: string;
  settings: {
    preferred_units: {
      weight: string;
      height: string;
      glucose: string;
    };
  };
}

export interface Tenant {
  id: string;
  name: string;
  settings: {
    data_retention_days: number;
    unit_system: 'metric' | 'imperial';
  };
  created_at: string;
}

export interface Alert {
  id: string;
  type: string;
  patient_id: string;
  threshold?: number;
  enabled: boolean;
  created_at: string;
  last_triggered?: string;
}

export interface TelemetryData {
  timestamp: string;
  heart_rate?: number;
  steps?: number;
  calories?: number;
  distance?: number;
  sleep?: {
    duration: number;
    quality: number;
  };
}