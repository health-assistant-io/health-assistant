import api from '../api/axios';

export async function getCurrentUser(): Promise<User> {
  const response = await api.get<User>('/users/me');
  return response.data;
}

export async function getUser(userId: string): Promise<User> {
  const response = await api.get<User>(`/users/${userId}`);
  return response.data;
}

export async function updateUser(userId: string, userData: Partial<User>): Promise<User> {
  const response = await api.put<User>(`/users/${userId}`, userData);
  return response.data;
}

export async function deleteUser(userId: string): Promise<{ message: string }> {
  const response = await api.delete<{ message: string }>(`/users/${userId}`);
  return response.data;
}

export async function listUsers(): Promise<User[]> {
  const response = await api.get<User[]>('/users');
  return response.data;
}

export async function createUser(userData: any): Promise<User> {
  const response = await api.post<User>('/users', userData);
  return response.data;
}

export type UserRole = 'SYSTEM_ADMIN' | 'ADMIN' | 'MANAGER' | 'USER';

export interface User {
  id: string;
  email: string;
  role: UserRole;
  tenant_id: string;
  settings: {
    preferred_units?: {
      weight: string;
      height: string;
      glucose: string;
    };
    ai_config?: {
      ocr?: {
        provider?: string;
        api_key?: string;
        api_base?: string;
        model?: string;
      };
      nlp?: {
        provider?: string;
        api_key?: string;
        api_base?: string;
        model?: string;
      };
    };
  };
}