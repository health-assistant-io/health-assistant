import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import { ShieldCheck, Save, Stethoscope } from 'lucide-react';
import { getTenant, updateMyTenant } from '../../services/tenantService';
import { listPatients } from '../../services/patientService';
import {
  listPatientAccess,
  type AccessGrant,
  type GrantStatus,
} from '../../services/patientAccessService';
import type { Patient } from '../../types/patient';
import { useAccessStore } from '../../store/slices/accessSlice';
import { PageHeader } from '../../components/ui/PageHeader';
import { LoadingState } from '../../components/ui/LoadingState';
import { AccessRequestsCard } from '../../components/account/AccessRequestsCard';
import { apiErrorDetail } from '../../utils/apiErrors';

type ManagerScope = 'tenant' | 'assigned';

const SETTLED_STATUSES: GrantStatus[] = ['revoked', 'expired', 'declined'];

function patientLabel(patient: Patient): string {
  const name = patient.name as { given?: string[]; family?: string; text?: string } | undefined;
  if (!name) return patient.id;
  if ('text' in name && name.text) return name.text;
  return [name.given?.join(' '), name.family].filter(Boolean).join(' ').trim() || patient.id;
}

function patientDob(patient: Patient): string {
  return patient.birth_date ? ` (${patient.birth_date})` : '';
}

/**
 * Tenant Access administration (ADR 0054 Phase 2 UI).
 *
 * Readable with `settings:read` or `patient:grant`; the tenant policy card
 * writes through the tenant-admin self-service endpoint (`settings:write`,
 * ADMIN/SYSTEM_ADMIN — MANAGER gets a read-only view). Grants are inspected
 * per patient through the patient's ledger — the tenant-wide grant list is
 * a later phase (plan D Phase 3); the caller's own incoming requests ride
 * the same `/me/access` surface as the self-service card.
 */
export function AccessManagement() {
  const { t } = useTranslation();
  const tenant = useAccessStore((s) => s.tenant);
  const can = useAccessStore((s) => s.can);

  const canReadSettings = can('settings:read');
  const canWriteSettings = can('settings:write');
  const canInspectGrants = can('patient:grant');

  return (
    <div className="space-y-8 pb-10">
      <PageHeader
        title={t('access.admin_title', 'Access')}
        subtitle={t('access.admin_subtitle', 'Care-team grants and record-sharing policy for this tenant.')}
        icon={<ShieldCheck className="w-8 h-8" />}
      />

      {canReadSettings && tenant && <TenantPolicyCard tenantId={tenant.id} canWrite={canWriteSettings} />}

      {canInspectGrants && <AccessRequestsCard />}

      {canInspectGrants && <GrantInspector />}
    </div>
  );
}

function TenantPolicyCard({ tenantId, canWrite }: { tenantId: string; canWrite: boolean }) {
  const { t } = useTranslation();
  const [managerScope, setManagerScope] = useState<ManagerScope>('tenant');
  const [allowShare, setAllowShare] = useState(true);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const tenant = await getTenant(tenantId);
        if (cancelled) return;
        const scope = tenant.settings?.manager_scope;
        setManagerScope(scope === 'assigned' ? 'assigned' : 'tenant');
        setAllowShare(tenant.settings?.allow_patient_share !== false);
      } catch (err) {
        if (!cancelled) toast.error(apiErrorDetail(err) || t('access.policy_load_failed', 'Could not load tenant policy.'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [tenantId, t]);

  const save = async () => {
    setSaving(true);
    try {
      const tenant = await getTenant(tenantId);
      await updateMyTenant(tenantId, {
        settings: {
          ...(tenant.settings ?? {}),
          manager_scope: managerScope,
          allow_patient_share: allowShare,
        },
      });
      setDirty(false);
      toast.success(t('access.policy_saved_toast', 'Tenant policy saved.'));
    } catch (err) {
      toast.error(apiErrorDetail(err) || t('access.policy_save_failed', 'Could not save the tenant policy.'));
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <LoadingState variant="inline" showText={false} message="" />;

  return (
    <div className="bg-white dark:bg-dark-surface rounded-3xl shadow-xs border border-gray-100 dark:border-dark-border overflow-hidden" data-testid="tenant-policy-card">
      <div className="px-8 py-6 border-b border-gray-100 dark:border-dark-border flex items-center justify-between bg-gray-50/30 dark:bg-dark-bg/20">
        <h3 className="text-lg font-bold text-brand-navy dark:text-dark-text tracking-tight">
          {t('access.policy_title', 'Record-sharing policy')}
        </h3>
        {!canWrite && (
          <span className="px-2.5 py-1 rounded-full bg-gray-100 dark:bg-dark-bg text-[10px] font-black uppercase tracking-widest text-gray-400">
            {t('access.read_only_badge', 'Read-only')}
          </span>
        )}
      </div>

      <div className="p-8 space-y-8">
        <div>
          <h4 className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1">
            {t('access.manager_scope_label', 'Manager reach')}
          </h4>
          <p className="text-xs text-gray-500 dark:text-dark-muted mb-3">
            {t('access.manager_scope_help', 'Whether managers see every record in the tenant or only their own plus care-team grants.')}
          </p>
          <div className="flex gap-2">
            {(['tenant', 'assigned'] as ManagerScope[]).map((scope) => (
              <button
                key={scope}
                type="button"
                disabled={!canWrite}
                onClick={() => {
                  setManagerScope(scope);
                  setDirty(true);
                }}
                className={`px-4 py-2 rounded-xl text-xs font-bold transition-colors disabled:opacity-60 ${
                  managerScope === scope
                    ? 'bg-blue-600 text-white'
                    : 'bg-gray-50 dark:bg-dark-bg text-gray-600 dark:text-dark-muted hover:bg-gray-100'
                }`}
              >
                {scope === 'tenant'
                  ? t('access.manager_scope_tenant', 'Whole tenant')
                  : t('access.manager_scope_assigned', 'Assigned only')}
              </button>
            ))}
          </div>
        </div>

        <div>
          <h4 className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1">
            {t('access.allow_share_label', 'Patient-initiated sharing')}
          </h4>
          <p className="text-xs text-gray-500 dark:text-dark-muted mb-3">
            {t('access.allow_share_help', 'Allow patients to invite staff to their record (the invitee must accept).')}
          </p>
          <label className="inline-flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              role="switch"
              data-testid="allow-patient-share"
              checked={allowShare}
              disabled={!canWrite}
              onChange={(e) => {
                setAllowShare(e.target.checked);
                setDirty(true);
              }}
              className="sr-only peer"
            />
            <span
              aria-hidden
              className="w-10 h-6 rounded-full bg-gray-200 dark:bg-dark-border peer-checked:bg-blue-600 relative transition-colors after:absolute after:top-0.5 after:left-0.5 after:w-5 after:h-5 after:rounded-full after:bg-white after:transition-transform peer-checked:after:translate-x-4"
            />
            <span className="text-sm font-bold text-gray-700 dark:text-dark-text">
              {allowShare
                ? t('access.allow_share_on', 'Allowed')
                : t('access.allow_share_off', 'Disabled')}
            </span>
          </label>
        </div>

        {canWrite && dirty && (
          <button
            onClick={save}
            disabled={saving}
            className="flex items-center space-x-2 px-4 py-2 bg-blue-600 text-white rounded-xl font-bold text-sm hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            <Save className="w-4 h-4" />
            <span>{t('common.save', 'Save')}</span>
          </button>
        )}
      </div>
    </div>
  );
}

function GrantInspector() {
  const { t } = useTranslation();
  const [patients, setPatients] = useState<Patient[] | null>(null);
  const [patientFilter, setPatientFilter] = useState('');
  const [selectedPatientId, setSelectedPatientId] = useState('');
  const [grants, setGrants] = useState<AccessGrant[] | null>(null);
  const [statusFilter, setStatusFilter] = useState<GrantStatus | 'all' | 'live'>('all');
  const [granteeFilter, setGranteeFilter] = useState('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const page = await listPatients(undefined, 200, 0);
        if (!cancelled) setPatients(page.items ?? []);
      } catch {
        if (!cancelled) setPatients([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const filteredPatients = useMemo(() => {
    const needle = patientFilter.trim().toLowerCase();
    if (!needle) return patients ?? [];
    return (patients ?? []).filter((patient) =>
      `${patientLabel(patient)} ${patient.mrn ?? ''}`.toLowerCase().includes(needle)
    );
  }, [patients, patientFilter]);

  const loadGrants = useCallback(async (patientId: string) => {
    setGrants(null);
    if (!patientId) return;
    try {
      setGrants(await listPatientAccess(patientId));
    } catch (err) {
      toast.error(apiErrorDetail(err) || t('access.load_failed', 'Could not load the access list.'));
      setGrants([]);
    }
  }, [t]);

  useEffect(() => {
    if (selectedPatientId) loadGrants(selectedPatientId);
  }, [selectedPatientId, loadGrants]);

  const visibleGrants = useMemo(() => {
    const needle = granteeFilter.trim().toLowerCase();
    return (grants ?? []).filter((grant) => {
      if (statusFilter === 'live' && !['active', 'pending'].includes(grant.status)) return false;
      if (statusFilter !== 'all' && statusFilter !== 'live' && grant.status !== statusFilter) return false;
      if (needle && !(grant.grantee_label ?? '').toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [grants, statusFilter, granteeFilter]);

  const selectedPatient = (patients ?? []).find((p) => p.id === selectedPatientId);

  return (
    <div className="bg-white dark:bg-dark-surface rounded-3xl shadow-xs border border-gray-100 dark:border-dark-border overflow-hidden" data-testid="grant-inspector">
      <div className="px-8 py-6 border-b border-gray-100 dark:border-dark-border bg-gray-50/30 dark:bg-dark-bg/20">
        <h3 className="text-lg font-bold text-brand-navy dark:text-dark-text tracking-tight">
          {t('access.inspector_title', 'Grant ledger by patient')}
        </h3>
        <p className="text-xs text-gray-500 dark:text-dark-muted mt-0.5">
          {t('access.inspector_help', 'Pick a record to see who can reach it.')}
        </p>
      </div>

      <div className="p-8 space-y-6">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1.5">
              {t('access.inspector_patient', 'Patient')}
            </label>
            <input
              value={patientFilter}
              onChange={(e) => setPatientFilter(e.target.value)}
              placeholder={t('access.inspector_patient_search', 'Search by name or MRN…')}
              className="w-full px-3 py-2.5 rounded-xl border border-gray-200 dark:border-dark-border bg-white dark:bg-dark-surface text-sm font-semibold text-gray-700 dark:text-dark-text"
            />
            {patientFilter.trim() && (
              <div className="mt-2 max-h-48 overflow-y-auto rounded-xl border border-gray-100 dark:border-dark-border divide-y divide-gray-50 dark:divide-dark-border">
                {filteredPatients.length === 0 && (
                  <p className="p-3 text-xs text-gray-400">{t('access.inspector_no_patients', 'No matching records.')}</p>
                )}
                {filteredPatients.slice(0, 20).map((patient) => (
                  <button
                    key={patient.id}
                    onClick={() => setSelectedPatientId(patient.id)}
                    className={`w-full text-left px-3 py-2 text-sm font-semibold transition-colors ${
                      selectedPatientId === patient.id
                        ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300'
                        : 'hover:bg-gray-50 dark:hover:bg-dark-bg text-gray-700 dark:text-dark-text'
                    }`}
                  >
                    {patientLabel(patient)}
                    <span className="text-xs text-gray-400">{patientDob(patient)}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label
                htmlFor="access-status-filter"
                className="block text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1.5"
              >
                {t('access.filter_status', 'Status')}
              </label>
              <select
                id="access-status-filter"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value as GrantStatus | 'all' | 'live')}
                className="w-full px-3 py-2.5 rounded-xl border border-gray-200 dark:border-dark-border bg-white dark:bg-dark-surface text-sm font-semibold text-gray-700 dark:text-dark-text"
              >
                <option value="all">{t('access.filter_all', 'All')}</option>
                <option value="live">{t('access.filter_live', 'Active + pending')}</option>
                <option value="active">{t('access.status_active', 'Active')}</option>
                <option value="pending">{t('access.status_pending', 'Pending')}</option>
                {SETTLED_STATUSES.map((status) => (
                  <option key={status} value={status}>
                    {t(`access.status_${status}`, status)}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label
                htmlFor="access-grantee-filter"
                className="block text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1.5"
              >
                {t('access.filter_grantee', 'Grantee')}
              </label>
              <input
                id="access-grantee-filter"
                value={granteeFilter}
                onChange={(e) => setGranteeFilter(e.target.value)}
                placeholder={t('access.filter_grantee_ph', 'Filter by name…')}
                className="w-full px-3 py-2.5 rounded-xl border border-gray-200 dark:border-dark-border bg-white dark:bg-dark-surface text-sm font-semibold text-gray-700 dark:text-dark-text"
              />
            </div>
          </div>
        </div>

        {selectedPatientId && grants === null && <LoadingState variant="inline" showText={false} message="" />}

        {grants !== null && (
          <div className="rounded-2xl border border-gray-100 dark:border-dark-border overflow-hidden">
            <div className="px-4 py-3 bg-gray-50/70 dark:bg-dark-bg/50 text-xs font-black uppercase tracking-widest text-gray-400">
              {selectedPatient ? patientLabel(selectedPatient) : selectedPatientId} · {visibleGrants.length}
            </div>
            {visibleGrants.length === 0 ? (
              <div className="p-8 text-center">
                <Stethoscope className="w-8 h-8 text-gray-300 dark:text-dark-border mx-auto mb-2" />
                <p className="text-sm text-gray-400">{t('access.inspector_empty', 'No grants match these filters.')}</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-[10px] font-black uppercase tracking-widest text-gray-400 border-b border-gray-100 dark:border-dark-border">
                      <th className="px-4 py-2.5">{t('access.col_grantee', 'Grantee')}</th>
                      <th className="px-4 py-2.5">{t('access.col_permission', 'Permission')}</th>
                      <th className="px-4 py-2.5">{t('access.col_source', 'Source')}</th>
                      <th className="px-4 py-2.5">{t('access.col_status', 'Status')}</th>
                      <th className="px-4 py-2.5">{t('access.col_expires', 'Expires')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50 dark:divide-dark-border">
                    {visibleGrants.map((grant) => (
                      <tr key={grant.id} data-testid="inspector-grant-row">
                        <td className="px-4 py-2.5 font-bold text-gray-700 dark:text-dark-text">
                          {grant.grantee_label || '—'}
                        </td>
                        <td className="px-4 py-2.5">{t(`access.permission_${grant.permission}`, grant.permission)}</td>
                        <td className="px-4 py-2.5 text-gray-500">{t(`access.source_${grant.source}`, grant.source)}</td>
                        <td className="px-4 py-2.5">{t(`access.status_${grant.status}`, grant.status)}</td>
                        <td className="px-4 py-2.5 text-gray-500">
                          {grant.expires_at
                            ? new Date(grant.expires_at).toLocaleDateString(undefined, { dateStyle: 'medium' })
                            : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default AccessManagement;
