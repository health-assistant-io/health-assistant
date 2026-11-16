import api from '../api/axios';

/**
 * The seed source lane client (ADR 0056 — `/admin/seed-sources`).
 *
 * Every failure is a typed detail object `{"code": ..., "message": ...}`
 * (see `docs/API.md` §`admin/seed-sources`); `seedApiError` extracts it for
 * the UI's error banners. All calls are server-enforced — this client only
 * shapes payloads and reads views.
 */

export type SeedSourceKind = 'bundled' | 'file' | 'git' | 'registry';
export type SeedSourceScope = 'system' | 'tenant';
export type SeedVersionStatus = 'pending' | 'approved' | 'rejected' | 'revoked';
export type SeedRunStatus = 'ok' | 'partial' | 'failed' | 'conflicted' | 'running';

export interface SeedRunSummary {
  run_id: string;
  mode: 'plan' | 'apply' | null;
  status: SeedRunStatus | null;
  started_at: string | null;
  stats: Record<string, number>;
}

export interface SeedSourceVersion {
  id: string;
  source_id: string;
  digest: string;
  commit_sha: string | null;
  etag: string | null;
  status: SeedVersionStatus;
  reason: string | null;
  approved_by: string | null;
  approved_at: string | null;
  contract_version: string | null;
  created_at: string;
  updated_at: string;
  pack_id: string | null;
  pack_version: string | null;
  pack_scope: string | null;
  pack_priority: number | null;
  pack_files: number | null;
  is_current: boolean;
  latest_run: SeedRunSummary | null;
}

export interface SeedSource {
  id: string;
  tenant_id: string | null;
  kind: SeedSourceKind;
  uri: string | null;
  ref_spec: string | null;
  subpath: string | null;
  priority: number;
  scope: SeedSourceScope;
  enabled: boolean;
  auth_secret_ref: string | null;
  current_version_id: string | null;
  last_check_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  versions: SeedSourceVersion[];
}

export interface SeedPlanEntry {
  stage: string;
  entity_type: string | null;
  seed_key: string | null;
  entity_id: string | null;
  action: 'create' | 'update' | 'noop' | 'skip' | 'conflict' | 'retire';
  content_hash: string | null;
  update_available: boolean;
  conflict: Record<string, unknown> | null;
  warnings: string[];
}

export interface SeedPlanDoc {
  pack: { id: string; version: string; digest: string; scope: string; priority: number };
  source: { id: string; kind: string };
  stages: Record<string, SeedPlanEntry[]>;
  retire: Array<Record<string, unknown>>;
  warnings: string[];
  errors: string[];
  summary: {
    create: number;
    update: number;
    noop: number;
    skip: number;
    conflict: number;
    retire: number;
    warnings: number;
    errors: number;
  };
}

export interface SeedRunItem {
  id: string;
  run_id: string;
  entity_type: string;
  entity_id: string | null;
  seed_key: string | null;
  action: string;
  content_hash: string | null;
  conflict: Record<string, unknown>;
}

export interface SeedRun {
  id: string;
  source_version_id: string;
  mode: 'plan' | 'apply' | null;
  status: SeedRunStatus | null;
  error: string | null;
  stats: Record<string, number>;
  plan: SeedPlanDoc | Record<string, never>;
  started_at: string | null;
  finished_at: string | null;
  digest?: string | null;
  items?: SeedRunItem[];
}

export interface ApplyOutcome {
  mode: 'apply';
  runs: Record<string, string>;
  statuses: Record<string, SeedRunStatus | null>;
  stats: Record<string, unknown>;
}

export interface CreateSourcePayload {
  kind: Exclude<SeedSourceKind, 'bundled'>;
  priority: number;
  scope?: SeedSourceScope;
  uri?: string | null;
  ref_spec?: string | null;
  subpath?: string | null;
  auth_secret_ref?: string | null;
  tenant_id?: string | null;
}

/** The typed `{code, message}` detail the lane always returns. */
export function seedApiError(err: unknown): { code: string; message: string } | null {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (
    detail &&
    typeof detail === 'object' &&
    typeof (detail as { code?: unknown }).code === 'string' &&
    typeof (detail as { message?: unknown }).message === 'string'
  ) {
    return { code: (detail as { code: string }).code, message: (detail as { message: string }).message };
  }
  return null;
}

export async function listSeedSources(): Promise<SeedSource[]> {
  const { data } = await api.get<SeedSource[]>('/admin/seed-sources');
  return data;
}

export async function createSeedSource(payload: CreateSourcePayload): Promise<SeedSource> {
  const { data } = await api.post<SeedSource>('/admin/seed-sources', payload);
  return data;
}

export async function syncSeedSource(sourceId: string): Promise<Record<string, unknown>> {
  const { data } = await api.post(`/admin/seed-sources/${sourceId}/sync`);
  return data;
}

export async function uploadSeedPack(
  sourceId: string,
  file: File,
  meta: { packId?: string; version?: string; license?: string } = {},
): Promise<SeedSourceVersion> {
  const form = new FormData();
  form.append('file', file);
  if (meta.packId) form.append('pack_id', meta.packId);
  if (meta.version) form.append('version', meta.version);
  if (meta.license) form.append('license', meta.license);
  const { data } = await api.post<SeedSourceVersion>(`/admin/seed-sources/${sourceId}/upload`, form);
  return data;
}

export async function listSeedVersions(sourceId: string): Promise<SeedSourceVersion[]> {
  const { data } = await api.get<SeedSourceVersion[]>(`/admin/seed-sources/${sourceId}/versions`);
  return data;
}

export async function approveSeedVersion(versionId: string): Promise<SeedSourceVersion> {
  const { data } = await api.post(`/admin/seed-sources/versions/${versionId}/approve`);
  return data;
}

export async function rejectSeedVersion(versionId: string, reason?: string): Promise<SeedSourceVersion> {
  const { data } = await api.post(`/admin/seed-sources/versions/${versionId}/reject`, {
    reason: reason ?? null,
  });
  return data;
}

export async function revokeSeedVersion(versionId: string): Promise<Record<string, unknown>> {
  const { data } = await api.post(`/admin/seed-sources/versions/${versionId}/revoke`);
  return data;
}

export async function planSeedVersion(versionId: string): Promise<SeedPlanDoc> {
  const { data } = await api.get<SeedPlanDoc>(`/admin/seed-sources/versions/${versionId}/plan`);
  return data;
}

export async function applySeedVersion(
  versionId: string,
  apply: boolean,
): Promise<{ mode: 'plan'; plan: SeedPlanDoc } | ApplyOutcome> {
  const { data } = await api.post(`/admin/seed-sources/versions/${versionId}/apply`, {
    apply,
  });
  return data;
}

export async function adoptSeedUpdates(
  sourceId: string,
  seedKey?: string,
): Promise<Record<string, unknown>> {
  const { data } = await api.post(`/admin/seed-sources/${sourceId}/adopt`, {
    seed_key: seedKey ?? null,
  });
  return data;
}

export async function setSeedSourceEnabled(sourceId: string, enabled: boolean): Promise<SeedSource> {
  const { data } = await api.post(`/admin/seed-sources/${sourceId}/${enabled ? 'enable' : 'disable'}`);
  return data;
}

export async function retireSeedSource(sourceId: string): Promise<Record<string, unknown>> {
  const { data } = await api.post(`/admin/seed-sources/${sourceId}/retire`);
  return data;
}

export async function listSeedRuns(sourceId: string, limit = 25): Promise<SeedRun[]> {
  const { data } = await api.get<SeedRun[]>(`/admin/seed-sources/${sourceId}/runs`, {
    params: { limit },
  });
  return data;
}

export async function getSeedRun(sourceId: string, runId: string): Promise<SeedRun> {
  const { data } = await api.get<SeedRun>(`/admin/seed-sources/${sourceId}/runs/${runId}`);
  return data;
}
