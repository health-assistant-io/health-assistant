import axiosInstance from '../api/axios';

export interface PatientLayout {
  id: string;
  user_id: string;
  patient_id: string;
  tenant_id: string;
  name: string;
  is_default: boolean;
  layout_config: any;
  cards_config: any[];
}

export const getPatientLayouts = async (patientId: string): Promise<PatientLayout[]> => {
  const response = await axiosInstance.get(`/patients/${patientId}/layouts`);
  return response.data;
};

export const getActiveLayout = async (patientId: string): Promise<PatientLayout> => {
  const response = await axiosInstance.get(`/patients/${patientId}/layouts/active`);
  return response.data;
};

export const createPatientLayout = async (patientId: string, data: Partial<PatientLayout>): Promise<PatientLayout> => {
  try {
    const response = await axiosInstance.post(`/patients/${patientId}/layouts`, { ...data, patient_id: patientId });
    return response.data;
  } catch (error: any) {
    if (error.response?.status === 404) {
      console.error('Patient not found - cannot create layout. Please ensure the patient exists in the system.');
      throw new Error(`Patient with ID ${patientId} not found. Please create the patient first.`);
    }
    throw error;
  }
};

export const updatePatientLayout = async (patientId: string, layoutId: string, data: Partial<PatientLayout>): Promise<PatientLayout> => {
  const response = await axiosInstance.put(`/patients/${patientId}/layouts/${layoutId}`, data);
  return response.data;
};

export const deletePatientLayout = async (patientId: string, layoutId: string): Promise<void> => {
  await axiosInstance.delete(`/patients/${patientId}/layouts/${layoutId}`);
};
