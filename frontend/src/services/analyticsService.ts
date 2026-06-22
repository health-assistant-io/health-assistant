import api from '../api/axios';
import { db } from './db';
import { DataSourceType } from '../types/biomarker';

export async function getDashboardData(
  _tenantId: string,
  patientId?: string,
  period: string = 'last-30-days'
): Promise<{
  recent_documents: Array<{
    id: string;
    filename: string;
    created_at: string;
  }>;
  upcoming_appointments: Array<{
    id: string;
    title: string;
    date: string;
  }>;
  alerts: Array<{
    type: string;
    message: string;
    timestamp: string;
  }>;
  summary: {
    total_documents: number;
    total_observations: number;
    last_upload: string;
  };
  latest_examination?: {
    id: string;
    examination_date: string;
    notes: string;
    category: string;
    doctor_name: string;
  };
  latest_imaging?: Array<{
    id: string;
    date: string;
    title: string;
    status: string;
    conclusion?: string;
  }>;
  latest_labs?: Array<{
    name: string;
    result: any;
    unit: string;
    date: string;
    status: string;
  }>;
}> {
  try {
    const response = await api.get<{
      recent_documents: Array<{ id: string; filename: string; created_at: string }>;
      upcoming_appointments: Array<{ id: string; title: string; date: string }>;
      alerts: Array<{ type: string; message: string; timestamp: string }>;
      summary: { total_documents: number; total_observations: number; last_upload: string };
      latest_examination?: any;
      latest_imaging?: any[];
      latest_labs?: any[];
    }>(
      '/analytics/dashboard',
      {
        params: {
          patient_id: patientId,
          period
        }
      }
    );
    
    // Cache the dashboard data if we have patientId
    if (patientId && response.data) {
       db.dashboardData.put({
          patient_id: patientId,
          period,
          data: response.data,
          updatedAt: Date.now()
       });
    }
    
    return response.data;
  } catch (error) {
    if (!navigator.onLine && patientId) {
      const cached = await db.dashboardData.where({ patient_id: patientId, period }).first();
      if (cached) return cached.data;
    }
    throw error;
  }
}

export async function getCachedDashboardData(patientId: string, period: string) {
  const cached = await db.dashboardData.where({ patient_id: patientId, period }).first();
  return cached?.data || null;
}

export async function getBiomarkerTrends(
  _tenantId: string,
  biomarkerCodes?: string,
  period: string = 'last-6-months',
  patientId?: string,
  aggregation?: string
): Promise<{
  biomarkers: {
    [key: string]: Array<{
      date: string;
      value: number;
      unit: string;
      name: string;
      examination_id?: string;
      examination_name?: string;
      status?: string;
      source_type?: DataSourceType;
      source_name?: string;
      source_id?: string;
      source_category?: string;
    }>;
  };
}> {
  try {
    const response = await api.get<{
      biomarkers: {
        [key: string]: Array<{
          date: string;
          value: number;
          unit: string;
          name: string;
        }>;
      };
    }>(`/analytics/trends`, {
      params: {
        biomarker_codes: biomarkerCodes,
        period,
        patient_id: patientId,
        aggregation: aggregation
      }
    });
    
    // Proactively cache if we are online and have patient context
    if (patientId && response.data && !biomarkerCodes) {
       db.biomarkerTrends.put({
          patient_id: patientId,
          period,
          data: response.data,
          updatedAt: Date.now()
       });
    }
    
    return response.data;
  } catch (error) {
    if (!navigator.onLine && patientId && !biomarkerCodes) {
      const cached = await db.biomarkerTrends.where({ patient_id: patientId, period }).first();
      if (cached) return cached.data;
    }
    throw error;
  }
}

export async function getCachedBiomarkerTrends(patientId: string, period: string) {
  const cached = await db.biomarkerTrends.where({ patient_id: patientId, period }).first();
  return cached?.data || null;
}

export async function getAnalyticsSummary(
  _tenantId: string,
  patientId?: string,
  period: string = 'last-year'
): Promise<{
  total_documents: number;
  total_observations: number;
  total_medications: number;
  active_alerts: number;
  last_upload: string;
}> {
  const response = await api.get<{
    total_documents: number;
    total_observations: number;
    total_medications: number;
    active_alerts: number;
    last_upload: string;
  }>(
    '/analytics/summary',
    {
      params: {
        patient_id: patientId,
        period
      }
    }
  );
  return response.data;
}

export async function getReferenceRanges(): Promise<{
  [key: string]: {
    min: number;
    max: number;
    unit: string;
  };
}> {
  const response = await api.get<{
    [key: string]: { min: number; max: number; unit: string };
  }>(
    '/analytics/reference-ranges'
  );
  return response.data;
}
export async function getCategoryAnalytics(
  categoryName: string,
  patientId?: string
): Promise<any> {
  const response = await api.get(`/analytics/category/${categoryName}`, {
    params: {
      patient_id: patientId
    }
  });
  return response.data;
}

export async function getAvailableCategories(patientId?: string): Promise<string[]> {
  const response = await api.get<{ categories: string[] }>('/analytics/available-categories', {
    params: {
      patient_id: patientId
    }
  });
  return response.data.categories;
}

export interface BiomarkerAnomaly {
  type: 'statistical_anomaly' | 'upward_trend' | 'downward_trend' | 'below_reference' | 'above_reference';
  message: string;
  severity: 'info' | 'warning' | 'critical';
  biomarker?: string;
  biomarker_slug?: string;
  biomarker_id?: string;
  value?: number;
  unit?: string;
}

export async function getAnomalies(patientId?: string, biomarkerCodes?: string): Promise<BiomarkerAnomaly[]> {
  try {
    const response = await api.get<{ anomalies: BiomarkerAnomaly[] }>('/analytics/anomalies', {
      params: { patient_id: patientId, biomarker_codes: biomarkerCodes }
    });
    return response.data.anomalies ?? [];
  } catch {
    return [];
  }
}
