/**
 * Unified instance search service — wraps the backend
 * ``GET /instances/search`` dispatcher (Phase 2).
 *
 * The instance counterpart of ``catalogService.searchCatalogs``. Returns
 * uniform ``InstanceSearchHit`` rows consumed by the generic
 * ``InstancePicker`` (inline type-ahead + browse modal "All types" mode) and
 * by each adapter's ``search()``.
 *
 * Security: the backend enforces tenant isolation + patient access + the
 * USER-tenant-wide 403 gate centrally; this client only forwards the
 * ``patientId`` the caller (the picker, bound to the current patient context)
 * provides.
 */
import api from '../api/axios';
import type { InstanceSearchHit, InstanceType } from '../components/instances/types';

export interface InstanceSearchQuery {
  q: string;
  patientId?: string;
  /** Restrict to a subset of entity types; omit for all registered. */
  types?: InstanceType[];
  limit?: number;
  offset?: number;
}

export async function searchInstances(
  query: InstanceSearchQuery,
): Promise<InstanceSearchHit[]> {
  const params: Record<string, string> = { q: query.q };
  if (query.patientId) params.patient_id = query.patientId;
  if (query.limit !== undefined) params.limit = String(query.limit);
  if (query.offset !== undefined) params.offset = String(query.offset);
  if (query.types && query.types.length > 0) {
    params.types = query.types.join(',');
  }
  const resp = await api.get<{ results: InstanceSearchHit[] }>(
    '/instances/search',
    { params },
  );
  return resp.data.results;
}
