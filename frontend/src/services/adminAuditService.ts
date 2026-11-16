/**
 * Admin audit surface (ADR 0054 Phase 3 — 8C viewer contract).
 *
 * `GET /admin/audit` + `/export` + `/retention` + `/verify`: ADMIN reads
 * their own tenant (a foreign `tenant_id` is a hidden 404 and `all=true`
 * is 403); SYSTEM_ADMIN sees every tenant plus the system chain. Rows
 * carry the hash-chain metadata so the viewer can show each entry's
 * tamper-evidence position.
 */
import api from '../api/axios';

/** One `audit_events` row (identity-auth §17 shape + chain metadata). */
export interface AuditEvent {
  id: string;
  tenant_id?: string | null;
  user_id?: string | null;
  action: string;
  resource_type: string;
  resource_id?: string | null;
  outcome: 'ok' | 'denied' | 'error' | string;
  /** Diffs arrive already redacted at write time (`[redacted]` markers). */
  old_value?: Record<string, unknown> | null; // # gate-allow: *, audit diff
  new_value?: Record<string, unknown> | null;
  created_at?: string | null;
  chain_key?: string | null;
  seq?: number | null;
  prev_hash?: string | null;
  entry_hash?: string | null;
}

export interface AuditListResponse {
  items: AuditEvent[];
  total: number;
}

export interface AuditFilters {
  tenant_id?: string | null;
  user_id?: string | null;
  resource_type?: string | null;
  resource_id?: string | null;
  action?: string | null;
  outcome?: 'ok' | 'denied' | 'error' | '' | null;
  date_from?: string | null;
  date_to?: string | null;
  /** Explicit system-wide stream (SYSTEM_ADMIN only). */
  all?: boolean;
  limit?: number;
  offset?: number;
}

export interface AuditRetentionPolicy {
  scope: string;
  scope_value: string;
  action: string;
  days: number;
  timestamp_column: string;
  enabled: boolean;
}

export interface AuditRetentionResponse {
  policies: AuditRetentionPolicy[];
  /** Deliberately `null` — there is no hold registry yet (ADR 0054 D3). */
  legal_holds: null;
  notes: string;
}

export interface AuditChainStatus {
  chain_key: string;
  ok: boolean;
  entries: number;
  head_seq: number | null;
  first_bad_seq: number | null;
  reason: string;
}

export interface AuditChainVerifyResponse {
  chain: AuditChainStatus;
}

export interface AuditChainVerifyAllResponse {
  ok: boolean;
  chains: AuditChainStatus[];
}

function toQuery(filters: AuditFilters, extra: Record<string, string> = {}): string {
  const sp = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    if (key === 'all' && value !== true) return;
    sp.append(key === 'all' ? 'all' : key, String(value));
  });
  Object.entries(extra).forEach(([key, value]) => sp.append(key, value));
  const s = sp.toString();
  return s ? `?${s}` : '';
}

export async function listAuditEvents(filters: AuditFilters = {}): Promise<AuditListResponse> {
  const r = await api.get<AuditListResponse>(`/admin/audit${toQuery(filters)}`);
  return r.data;
}

/** Trigger a browser download of the filtered stream (NDJSON or CSV).
 * The backend caps each export at 10000 rows, audits the export, and
 * reports a truncated stream via the `X-Audit-Export-Truncated` /
 * `X-Audit-Export-Total` response headers — the resolved truncation facts
 * are returned so the viewer can surface a narrowing hint. */
export async function exportAuditEvents(
  filters: AuditFilters = {},
  format: 'json' | 'csv' = 'json',
): Promise<{ truncated: boolean; totalMatched: number | null }> {
  const response = await api.get(`/admin/audit/export${toQuery(filters, { format })}`, {
    responseType: 'blob',
  });
  const blob = response.data as Blob;
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = format === 'csv' ? 'audit-export.csv' : 'audit-export.jsonl';
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
  const truncated = String(response.headers?.['x-audit-export-truncated'] ?? '') === 'true';
  const totalRaw = response.headers?.['x-audit-export-total'];
  const totalMatched = totalRaw != null && !Number.isNaN(Number(totalRaw)) ? Number(totalRaw) : null;
  return { truncated, totalMatched };
}

export async function getAuditRetention(): Promise<AuditRetentionResponse> {
  const r = await api.get<AuditRetentionResponse>('/admin/audit/retention');
  return r.data;
}

export async function verifyAuditChain(
  params: { tenantId?: string | null; all?: boolean } = {},
): Promise<AuditChainVerifyResponse | AuditChainVerifyAllResponse> {
  const sp = new URLSearchParams();
  if (params.all) sp.append('all', 'true');
  else if (params.tenantId) sp.append('tenant_id', params.tenantId);
  const qs = sp.toString();
  const r = await api.get(`/admin/audit/verify${qs ? `?${qs}` : ''}`);
  return r.data;
}
