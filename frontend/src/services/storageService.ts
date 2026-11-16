import api from '../api/axios';

/** Read-only deployment storage facts. System-admin only; the `refresh`
 * flag drops the 30 s health cache so the test button is real. */
export interface StorageStatus {
  backend: string;
  entrypoint: string;
  cloud_profile: string | null;
  bucket: string | null;
  container: string | null;
  region: string | null;
  endpoint_host: string | null;
  prefix: string;
  sse: string;
  max_object_mb: number;
  presign_ttl_seconds: number;
  required: boolean;
  status: 'ok' | 'degraded' | 'unavailable';
  healthy: boolean;
  detail: string | null;
  checked_at: string | null;
}

export async function fetchStorageStatus(
  refresh = false,
): Promise<StorageStatus> {
  const { data } = await api.get<StorageStatus>('/admin/system/storage', {
    params: refresh ? { refresh: true } : undefined,
  });
  return data;
}
