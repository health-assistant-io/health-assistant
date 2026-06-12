import api from '../api/axios';
import { Doctor, ContactPoint } from '../types/clinical';

export type { Doctor, ContactPoint };

export async function listDoctors(userId?: string): Promise<Doctor[]> {
  const response = await api.get<Doctor[]>('/doctors', {
    params: { user_id: userId }
  });
  return response.data;
}

export async function getDoctor(doctorId: string): Promise<Doctor> {
  const response = await api.get<Doctor>(`/doctors/${doctorId}`);
  return response.data;
}

export async function createDoctor(data: Partial<Doctor>): Promise<Doctor> {
  const response = await api.post<Doctor>('/doctors', data);
  return response.data;
}

export async function updateDoctor(doctorId: string, data: Partial<Doctor>): Promise<Doctor> {
  const response = await api.put<Doctor>(`/doctors/${doctorId}`, data);
  return response.data;
}

export async function deleteDoctor(doctorId: string): Promise<void> {
  await api.delete(`/doctors/${doctorId}`);
}
