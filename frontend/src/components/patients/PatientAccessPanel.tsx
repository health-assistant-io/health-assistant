import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { toast } from 'react-toastify';
import {
  ShieldCheck,
  UserPlus,
  History,
  Pencil,
  Trash2,
  ExternalLink,
  Stethoscope,
} from 'lucide-react';
import {
  listPatientAccess,
  createPatientAccess,
  updatePatientAccess,
  revokePatientAccess,
  grantSatisfies,
  type AccessGrant,
  type GrantPermission,
} from '../../services/patientAccessService';
import { listDoctors } from '../../services/doctorService';
import { listUsers } from '../../services/userService';
import type { Doctor } from '../../types/clinical';
import type { Patient } from '../../types/patient';
import { useAuthStore } from '../../store/slices/authSlice';
import { useAccessStore } from '../../store/slices/accessSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import { FormModal } from '../ui/FormModal';
import { DatePicker } from '../ui/DatePicker';
import { LoadingState } from '../ui/LoadingState';
import { apiErrorDetail } from '../../utils/apiErrors';

const STAFF_ROLES = ['ADMIN', 'MANAGER', 'SYSTEM_ADMIN'];
const LIVE_STATUSES = new Set(['active', 'pending']);

interface GranteeOption {
  id: string;
  label: string;
  sublabel?: string;
}

function useGranteeOptions(enabledUsers: boolean, enabledDoctors: boolean, staffOnly: boolean) {
  const [users, setUsers] = useState<GranteeOption[]>([]);
  const [doctors, setDoctors] = useState<GranteeOption[]>([]);
  const [optionsLoading, setOptionsLoading] = useState(false);

  useEffect(() => {
    if (!enabledUsers && !enabledDoctors) return;
    let cancelled = false;
    const loadOptions = async () => {
      setOptionsLoading(true);
      try {
        const [userRows, doctorRows] = await Promise.all([
          enabledUsers
            ? listUsers().catch(() => [])
            : Promise.resolve([] as never[]),
          enabledDoctors
            ? listDoctors().catch(() => [])
            : Promise.resolve([] as Doctor[]),
        ]);
        if (cancelled) return;
        setUsers(
          (userRows as any[])
            .filter((u) => (staffOnly ? ['ADMIN', 'MANAGER'].includes(u.role) : u.is_active !== false))
            .map((u) => ({ id: u.id, label: u.full_name || u.email, sublabel: u.email }))
        );
        setDoctors(
          (doctorRows as Doctor[])
            .filter((d) => !!d.user_id)
            .map((d) => ({ id: d.id, label: d.name, sublabel: d.specialty }))
        );
      } finally {
        if (!cancelled) setOptionsLoading(false);
      }
    };
    loadOptions();
    return () => {
      cancelled = true;
    };
  }, [enabledUsers, enabledDoctors, staffOnly]);

  return { users, doctors, optionsLoading };
}

type GranteeKind = 'user' | 'doctor' | 'guardian';

interface GrantFormState {
  mode: 'create' | 'edit';
  grant?: AccessGrant;
  granteeKind: GranteeKind;
  granteeId: string;
  permission: GrantPermission;
  expiresAt: string | null;
  reason: string;
}

const EMPTY_CREATE: GrantFormState = {
  mode: 'create',
  granteeKind: 'user',
  granteeId: '',
  permission: 'read',
  expiresAt: null,
  reason: '',
};

const PERMISSION_LABELS: Record<GrantPermission, string> = {
  read: 'Read',
  write: 'Write',
  admin: 'Admin',
};

function StatusBadge({ status }: { status: AccessGrant['status'] }) {
  const { t } = useTranslation();
  const tone: Record<AccessGrant['status'], string> = {
    active: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300',
    pending: 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
    revoked: 'bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300',
    expired: 'bg-gray-100 text-gray-500 dark:bg-dark-bg dark:text-dark-muted',
    declined: 'bg-gray-100 text-gray-500 dark:bg-dark-bg dark:text-dark-muted',
  };
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-widest ${tone[status] ?? tone.expired}`}
    >
      {t(`access.status_${status}`, status)}
    </span>
  );
}

function PermissionBadge({ permission }: { permission: GrantPermission }) {
  const { t } = useTranslation();
  const tone: Record<GrantPermission, string> = {
    read: 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
    write: 'bg-indigo-50 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300',
    admin: 'bg-purple-50 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300',
  };
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-widest ${tone[permission]}`}
    >
      {t(`access.permission_${permission}`, PERMISSION_LABELS[permission])}
    </span>
  );
}

function formatDate(value?: string | null): string {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString(undefined, { dateStyle: 'medium' });
}

interface PatientAccessPanelProps {
  patient: Patient;
}

export function PatientAccessPanel({ patient }: PatientAccessPanelProps) {
  const { t } = useTranslation();
  const patientId = patient.id;
  const user = useAuthStore((s) => s.user);
  const can = useAccessStore((s) => s.can);
  const canPatient = useAccessStore((s) => s.canPatient);
  const tenant = useAccessStore((s) => s.tenant);
  const showConfirmation = useUIStore((s) => s.showConfirmation);

  const [grants, setGrants] = useState<AccessGrant[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState<GrantFormState | null>(null);
  const [saving, setSaving] = useState(false);

  const isStaff = !!user && STAFF_ROLES.includes(user.role);
  const isAdminRole = user?.role === 'ADMIN' || user?.role === 'SYSTEM_ADMIN';
  const isOwner = !!user && !!patient.user_id && patient.user_id === user.id;
  const ownerless = !patient.user_id;
  const canGrantPermission = can('patient:grant');
  // Creators: staff, the owner sharing their own record, or a grantee whose
  // grant carries admin (the server's manage level for this surface).
  const canCreate = canGrantPermission && (isStaff || isOwner || canPatient(patientId, 'admin'));
  const shareMode = isOwner && !isStaff;

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      setGrants(await listPatientAccess(patientId));
    } catch (err) {
      setLoadError(apiErrorDetail(err) || t('access.load_failed', 'Could not load the access list.'));
      setGrants([]);
    }
  }, [patientId, t]);

  useEffect(() => {
    load();
  }, [load]);

  const [live, settled] = useMemo(() => {
    const all = grants ?? [];
    return [
      all.filter((g) => LIVE_STATUSES.has(g.status)),
      all.filter((g) => !LIVE_STATUSES.has(g.status)),
    ];
  }, [grants]);

  const canManageRow = (grant: AccessGrant): boolean => {
    if (!canGrantPermission || !LIVE_STATUSES.has(grant.status)) return false;
    if (isStaff) return true;
    // A non-staff owner manages only the shares they initiated (the
    // server's rule — `source=patient` rows only).
    return isOwner && grant.source === 'patient';
  };

  const openCreate = () => {
    setForm({ ...EMPTY_CREATE, granteeKind: shareMode ? 'doctor' : 'user' });
  };

  const openEdit = (grant: AccessGrant) => {
    setForm({
      mode: 'edit',
      grant,
      granteeKind: grant.grantee_doctor_id ? 'doctor' : 'user',
      granteeId: grant.grantee_user_id ?? grant.grantee_doctor_id ?? '',
      permission: grant.permission,
      expiresAt: grant.expires_at ?? null,
      reason: grant.reason ?? '',
    });
  };

  const confirmRevoke = (grant: AccessGrant) => {
    const label = grant.grantee_label || t('access.this_grantee', 'this member');
    showConfirmation({
      title: t('access.revoke_title', 'Revoke access'),
      message: t('access.revoke_confirm', {
        defaultValue: 'Revoke {{label}}’s access to this record? They lose access immediately.',
        label,
      }),
      confirmLabel: t('access.revoke', 'Revoke'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await revokePatientAccess(patientId, grant.id);
          toast.success(t('access.revoked_toast', 'Access revoked.'));
          await load();
        } catch (err) {
          toast.error(apiErrorDetail(err) || t('access.revoke_failed', 'Could not revoke access.'));
        }
      },
    });
  };

  const submitForm = async () => {
    if (!form) return;
    setSaving(true);
    try {
      if (form.mode === 'create') {
        const payload = {
          permission: form.permission,
          expires_at: form.expiresAt || null,
          reason: form.reason.trim() || null,
          ...(form.granteeKind === 'doctor'
            ? { grantee_doctor_id: form.granteeId }
            : { grantee_user_id: form.granteeId }),
          ...(form.granteeKind === 'guardian' ? { source: 'guardian' as const } : {}),
        };
        const created = await createPatientAccess(patientId, payload);
        toast.success(
          created.status === 'pending'
            ? t('access.share_requested_toast', 'Share request sent — pending their acceptance.')
            : t('access.granted_toast', 'Access granted.')
        );
      } else if (form.grant) {
        await updatePatientAccess(patientId, form.grant.id, {
          permission: form.permission,
          expires_at: form.expiresAt || null,
          reason: form.reason.trim() || null,
        });
        toast.success(t('access.updated_toast', 'Access updated.'));
      }
      setForm(null);
      await load();
    } catch (err) {
      toast.error(apiErrorDetail(err) || t('access.save_failed', 'Could not save the access change.'));
    } finally {
      setSaving(false);
    }
  };

  if (grants === null && !loadError) {
    return <LoadingState variant="inline" showText={false} message="" />;
  }

  return (
    <section aria-label={t('access.tab_title', 'Access')} className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div className="flex items-center space-x-3">
          <div className="p-2 bg-purple-50 dark:bg-purple-900/20 rounded-xl">
            <ShieldCheck className="w-5 h-5 text-purple-600" />
          </div>
          <div>
            <h3 className="text-lg font-bold text-brand-navy dark:text-dark-text tracking-tight">
              {t('access.tab_title', 'Access')}
            </h3>
            <p className="text-xs text-gray-500 dark:text-dark-muted">
              {shareMode
                ? t('access.owner_subtitle', 'Who can see this record — share it with your care team.')
                : t('access.staff_subtitle', 'Care-team access to this record.')}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {can('audit:read') && user?.role === 'SYSTEM_ADMIN' && tenant && (
            <Link
              to={`/admin/system/tenants/${tenant.id}`}
              className="flex items-center space-x-1.5 px-3 py-2 text-xs font-bold text-gray-500 dark:text-dark-muted hover:text-gray-700 dark:hover:text-dark-text rounded-xl border border-gray-200 dark:border-dark-border transition-colors"
            >
              <ExternalLink className="w-3.5 h-3.5" />
              <span>{t('access.audit_link', 'Audit log')}</span>
            </Link>
          )}
          {!loadError && canCreate && (
            <button
              onClick={openCreate}
              className="flex items-center space-x-2 px-4 py-2 bg-blue-600 text-white rounded-xl hover:bg-blue-700 transition-all font-bold text-sm active:scale-95"
            >
              <UserPlus className="w-4 h-4" />
              <span>
                {shareMode
                  ? t('access.share_with_doctor', 'Share with my doctor')
                  : t('access.grant_access', 'Grant access')}
              </span>
            </button>
          )}
        </div>
      </div>

      {loadError && (
        <div className="p-4 rounded-2xl bg-red-50 dark:bg-red-900/20 text-sm text-red-700 dark:text-red-300">
          {loadError}
        </div>
      )}

      {!loadError && live.length === 0 && settled.length === 0 && (
        <div className="p-10 bg-gray-50 dark:bg-dark-bg/50 rounded-3xl border border-dashed border-gray-200 dark:border-dark-border text-center">
          <Stethoscope className="w-10 h-10 text-gray-300 dark:text-dark-border mx-auto mb-3" />
          <p className="text-sm text-gray-500 dark:text-dark-muted">
            {shareMode
              ? t('access.empty_owner', 'Your record is private. Share it with your doctor to give them access.')
              : t('access.empty_staff', 'No one has been granted access to this record yet.')}
          </p>
          {canCreate && (
            <button
              onClick={openCreate}
              className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-xl font-bold text-sm hover:bg-blue-700 transition-colors"
            >
              {shareMode
                ? t('access.share_with_doctor', 'Share with my doctor')
                : t('access.grant_access', 'Grant access')}
            </button>
          )}
        </div>
      )}

      {live.length > 0 && (
        <div className="space-y-3">
          {live.map((grant) => (
            <GrantRow
              key={grant.id}
              grant={grant}
              manageable={canManageRow(grant)}
              onEdit={openEdit}
              onRevoke={confirmRevoke}
            />
          ))}
        </div>
      )}

      {settled.length > 0 && (
        <div className="pt-2">
          <h4 className="flex items-center text-[10px] font-black uppercase tracking-widest text-gray-400 mb-3">
            <History className="w-3 h-3 mr-2" />
            {t('access.history_title', 'History')}
          </h4>
          <div className="space-y-2 opacity-75">
            {settled.map((grant) => (
              <GrantRow key={grant.id} grant={grant} manageable={false} onEdit={openEdit} onRevoke={confirmRevoke} />
            ))}
          </div>
        </div>
      )}

      <GrantFormModal
        patientId={patientId}
        form={form}
        saving={saving}
        shareMode={shareMode}
        guardianAllowed={!!(ownerless && isAdminRole)}
        onClose={() => setForm(null)}
        onChange={setForm}
        onSubmit={submitForm}
      />
    </section>
  );
}

function GrantRow({
  grant,
  manageable,
  onEdit,
  onRevoke,
}: {
  grant: AccessGrant;
  manageable: boolean;
  onEdit: (grant: AccessGrant) => void;
  onRevoke: (grant: AccessGrant) => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      data-testid={`grant-row-${grant.id}`}
      className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 p-4 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-2xl shadow-xs"
    >
      <div className="min-w-0">
        <div className="flex items-center flex-wrap gap-2">
          <span className="font-bold text-sm text-brand-navy dark:text-dark-text truncate">
            {grant.grantee_label || t('access.unknown_grantee', 'Unknown member')}
          </span>
          <PermissionBadge permission={grant.permission} />
          <StatusBadge status={grant.status} />
          <span className="inline-block px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-widest bg-gray-100 dark:bg-dark-bg text-gray-500 dark:text-dark-muted">
            {t(`access.source_${grant.source}`, grant.source)}
          </span>
        </div>
        <div className="mt-1 text-[11px] text-gray-400 dark:text-dark-muted space-x-3">
          <span>
            {t('access.granted_at', 'Granted')} {formatDate(grant.granted_at)}
          </span>
          {grant.accepted_at && (
            <span>
              {t('access.accepted_at', 'Accepted')} {formatDate(grant.accepted_at)}
            </span>
          )}
          <span>
            {grant.expires_at
              ? t('access.expires_date', { defaultValue: 'Expires {{date}}', date: formatDate(grant.expires_at) })
              : t('access.no_expiry', 'No expiry')}
          </span>
        </div>
        {grant.reason && (
          <p className="mt-1 text-xs text-gray-500 dark:text-dark-muted italic truncate">{grant.reason}</p>
        )}
      </div>
      {manageable && (
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            onClick={() => onEdit(grant)}
            title={t('access.edit_grant', 'Edit access')}
            className="p-2 rounded-xl text-gray-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors"
          >
            <Pencil className="w-4 h-4" />
          </button>
          <button
            onClick={() => onRevoke(grant)}
            title={t('access.revoke', 'Revoke')}
            className="p-2 rounded-xl text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      )}
    </div>
  );
}

function GrantFormModal({
  patientId,
  form,
  saving,
  shareMode,
  guardianAllowed,
  onClose,
  onChange,
  onSubmit,
}: {
  patientId: string;
  form: GrantFormState | null;
  saving: boolean;
  shareMode: boolean;
  guardianAllowed: boolean;
  onClose: () => void;
  onChange: (form: GrantFormState | null) => void;
  onSubmit: () => void;
}) {
  const { t } = useTranslation();
  const isEdit = form?.mode === 'edit';
  const granteeKind = form?.granteeKind ?? 'user';
  const { users, doctors, optionsLoading } = useGranteeOptions(
    !!form && (isEdit || granteeKind === 'user' || granteeKind === 'guardian'),
    !!form && (isEdit || granteeKind === 'doctor'),
    granteeKind === 'user'
  );

  if (!form) return null;

  const granteeKinds: { value: GranteeKind; label: string }[] = [
    ...(shareMode
      ? ([{ value: 'doctor' as const, label: t('access.grantee_doctor', 'Doctor') }])
      : guardianAllowed
        ? [
            { value: 'user' as const, label: t('access.grantee_care_team', 'Care team') },
            { value: 'guardian' as const, label: t('access.grantee_guardian', 'Guardian') },
          ]
        : [
            { value: 'user' as const, label: t('access.grantee_care_team', 'Care team') },
            { value: 'doctor' as const, label: t('access.grantee_doctor', 'Doctor') },
          ]),
  ];

  const options = form.granteeKind === 'doctor' ? doctors : users;
  const canSubmit = isEdit || (!!form.granteeId && !optionsLoading);
  // Guardian grants are active immediately (only patient shares pend on
  // acceptance), so only a share in shareMode shows "Send request".
  const pendingCreate = !isEdit && shareMode;

  const update = (patch: Partial<GrantFormState>) => onChange({ ...form, ...patch });

  return (
    <FormModal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={
        isEdit
          ? t('access.edit_grant_title', 'Edit access')
          : shareMode
            ? t('access.share_with_doctor', 'Share with my doctor')
            : t('access.grant_access_title', 'Grant access')
      }
      description={
        shareMode && !isEdit
          ? t('access.share_description', 'They receive a request and must accept before any record is visible.')
          : undefined
      }
      icon={ShieldCheck}
      onSubmit={onSubmit}
      submitting={saving}
      submitDisabled={!canSubmit}
      submitLabel={
        isEdit
          ? t('common.save', 'Save')
          : pendingCreate
            ? t('access.send_request', 'Send request')
            : t('access.grant', 'Grant')
      }
    >
      <div className="space-y-4" data-testid={`access-form-${patientId}`}>
        {!isEdit && granteeKinds.length > 1 && (
          <div>
            <label className="block text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1.5">
              {t('access.grantee_kind', 'Give access to')}
            </label>
            <div className="flex gap-2">
              {granteeKinds.map((kind) => (
                <button
                  key={kind.value}
                  type="button"
                  onClick={() => update({ granteeKind: kind.value, granteeId: '' })}
                  className={`px-3 py-2 rounded-xl text-xs font-bold transition-colors ${
                    form.granteeKind === kind.value
                      ? 'bg-blue-600 text-white'
                      : 'bg-gray-50 dark:bg-dark-bg text-gray-600 dark:text-dark-muted hover:bg-gray-100'
                  }`}
                >
                  {kind.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {!isEdit && (
          <div>
            <label className="block text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1.5">
              {form.granteeKind === 'guardian'
                ? t('access.grantee_guardian_label', 'Guardian account')
                : form.granteeKind === 'doctor'
                  ? t('access.grantee_doctor_label', 'Doctor')
                  : t('access.grantee_user_label', 'Staff member')}
            </label>
            <select
              value={form.granteeId}
              onChange={(e) => update({ granteeId: e.target.value })}
              className="w-full px-3 py-2.5 rounded-xl border border-gray-200 dark:border-dark-border bg-white dark:bg-dark-surface text-sm font-semibold text-gray-700 dark:text-dark-text"
            >
              <option value="">{optionsLoading ? t('common.loading', 'Loading…') : t('access.pick_grantee', 'Select…')}</option>
              {options.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                  {option.sublabel ? ` — ${option.sublabel}` : ''}
                </option>
              ))}
            </select>
            {form.granteeKind === 'doctor' && (
              <p className="mt-1 text-[11px] text-gray-400">
                {t('access.doctor_linked_hint', 'Only doctors with a linked account can hold access.')}
              </p>
            )}
          </div>
        )}

        <div>
          <label className="block text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1.5">
            {t('access.permission_label', 'Permission')}
          </label>
          <div className="flex gap-2">
            {(['read', 'write', 'admin'] as GrantPermission[]).map((level) => (
              <button
                key={level}
                type="button"
                onClick={() => update({ permission: level })}
                className={`flex-1 px-3 py-2 rounded-xl text-xs font-bold transition-colors ${
                  form.permission === level
                    ? 'bg-blue-600 text-white'
                    : 'bg-gray-50 dark:bg-dark-bg text-gray-600 dark:text-dark-muted hover:bg-gray-100'
                }`}
              >
                {t(`access.permission_${level}`, PERMISSION_LABELS[level])}
              </button>
            ))}
          </div>
          <p className="mt-1 text-[11px] text-gray-400">
            {grantSatisfies(form.permission, 'write')
              ? t('access.permission_write_hint', 'Can view and update this record.')
              : t('access.permission_read_hint', 'Can view this record.')}
          </p>
        </div>

        <div>
          <label className="block text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1.5">
            {t('access.expires_label', 'Expires (optional)')}
          </label>
          <DatePicker
            value={form.expiresAt}
            onChange={(value) => update({ expiresAt: value })}
            allowClear
            clearLabel={t('access.no_expiry', 'No expiry')}
            placeholder={t('access.expires_placeholder', 'Select a date')}
            minDate={new Date()}
          />
        </div>

        <div>
          <label className="block text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1.5">
            {t('access.reason_label', 'Reason (optional)')}
          </label>
          <textarea
            value={form.reason}
            onChange={(e) => update({ reason: e.target.value })}
            rows={2}
            maxLength={500}
            className="w-full px-3 py-2.5 rounded-xl border border-gray-200 dark:border-dark-border bg-white dark:bg-dark-surface text-sm text-gray-700 dark:text-dark-text"
            placeholder={t('access.reason_placeholder', 'Why is this access needed?')}
          />
        </div>

        {isEdit && (
          <p className="text-[11px] text-gray-400 dark:text-dark-muted">
            {t(
              'access.edit_clear_hint',
              'Saving clears the expiry or reason when those fields are empty.'
            )}
          </p>
        )}
      </div>
    </FormModal>
  );
}
