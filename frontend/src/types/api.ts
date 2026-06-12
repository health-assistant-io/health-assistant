export interface ApiResponse<T> {
  data: T;
  status: number;
  message?: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  limit: number;
  hasMore: boolean;
}

export interface AuthResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface DocumentResponse {
  id: string;
  filename: string;
  file_path: string;
  status: string;
  progress?: number;
  created_at: string;
  patient_id?: string;
}

export interface ExtractionResponse {
  document_id: string;
  entities: {
    biomarkers: Array<{
      text: string;
      loinc_code?: string;
    }>;
    dates: string[];
    medications: string[];
    quantities: Array<{
      value: number;
      unit: string;
    }>;
  };
  status: string;
}