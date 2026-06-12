import { clearAuthData } from '../utils/auth';
import api from '../api/axios';

interface LoginCredentials {
  email: string;
  password: string;
}

interface AuthResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export async function login(credentials: LoginCredentials): Promise<AuthResponse> {
  const response = await api.post<AuthResponse>('/auth/login', credentials);
  return response.data;
}

export async function refreshToken(refreshToken: string): Promise<string> {
  const response = await api.post<{ access_token: string }>('/auth/refresh', { refresh_token: refreshToken });
  return response.data.access_token;
}

export async function validateToken(): Promise<boolean> {
  try {
    await api.get('/auth/validate');
    return true;
  } catch {
    return false;
  }
}

export function setTokens(accessToken: string, refreshToken: string): void {
  localStorage.setItem('accessToken', accessToken);
  localStorage.setItem('refreshToken', refreshToken);
}

export async function getAccessToken(): Promise<string | null> {
  return localStorage.getItem('accessToken');
}

export async function refreshAccessToken(): Promise<string> {
  const refreshTokenStr = localStorage.getItem('refreshToken');
  if (!refreshTokenStr) {
    throw new Error('No refresh token available');
  }
  
  const newToken = await refreshToken(refreshTokenStr);
  localStorage.setItem('accessToken', newToken);
  return newToken;
}

export async function logout(): Promise<void> {
  await clearAuthData();
  window.location.href = '/login';
}