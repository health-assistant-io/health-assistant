import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ArrowLeft, Globe, Users, Activity, FileText, Database, Save, X,
  ShieldCheck, Trash2, LogIn, Power, PowerOff, Copy, Loader2,
} from 'lucide-react';
import { toast } from 'react-toastify';
import {
  getTenantDetail, updateTenant, deactivateTenant, reactivateTenant,
  hardDeleteTenant, listTenantUsers, updateTenantUser, createTenantInvite,
  listTenantAudit,
  type TenantDetail, type TenantUser, type TenantUserListResponse,
  type AuditListResponse, type AuditEntry,
} from '../../services/tenantService';
import { performTenantSwitch } from '../../store/slices/tenantSwitchSlice';
import { useAuthStore } from '../../store/slices/authSlice';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import { PageHeader } from '../../components/ui/PageHeader';
import { LoadingState } from '../../components/ui/LoadingState';
import { useTranslation } from 'react-i18next';

type Tab = 'overview' | 'users' | 'audit' | 'settings';

function TenantDetail() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const showConfirmation = useUIStore((s) => s.showConfirmation);
  const login = useAuthStore((s) => s.login);
  const updateUser = useAuthStore((s) => s.updateUser);
  const clearPatientContext = usePatientStore((s) => s.clearPatientContext);

  const [detail, setDetail] = useState<TenantDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<Tab>('overview');
  const [switching, setSwitching] = useState(false);

  // Editable settings form
  const [form, setForm] = useState({ name: '', slug: '', description: '', settings: '{}' });
  const [savingSettings, setSavingSettings] = useState(false);

  // Hard-delete confirmation
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState('');
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    if (!tenantId) return;
    setIsLoading(true);
    try {
      const data = await getTenantDetail(tenantId);
      setDetail(data);
      setForm({
        name: data.name,
        slug: data.slug,
        description: data.description || '',
        settings: JSON.stringify(data.settings || {}, null, 2),
      });
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || t('admin.tenants.not_found'));
      navigate('/admin/system/tenants');
    } finally {
      setIsLoading(false);
    }
  }, [tenantId, navigate, t]);

  useEffect(() => { load(); }, [load]);

  const handleSwitch = async () => {
    if (!detail) return;
    setSwitching(true);
    try {
      await performTenantSwitch(detail.id, (access, refresh) => login(access, refresh));
      // Reflect the scoped tenant_id on the user object so downstream
      // patient-context fetchers and the Header pick up the new tenant.
      updateUser({ tenant_id: detail.id } as any);
      clearPatientContext();
      toast.success(t('admin.tenants.switched_into', { name: detail.name }));
      navigate('/dashboard', { replace: true });
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Switch failed');
    } finally {
      setSwitching(false);
    }
  };

  const handleSaveSettings = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!detail) return;
    let settingsObj: Record<string, any> = {};
    try {
      settingsObj = form.settings.trim() ? JSON.parse(form.settings) : {};
    } catch {
      toast.error('Invalid settings JSON');
      return;
    }
    setSavingSettings(true);
    try {
      await updateTenant(detail.id, {
        name: form.name,
        slug: form.slug || undefined,
        description: form.description || undefined,
        settings: settingsObj,
      });
      toast.success(t('admin.tenants.saved'));
      await load();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Save failed');
    } finally {
      setSavingSettings(false);
    }
  };

  const handleDeactivate = () => {
    if (!detail) return;
    showConfirmation({
      title: t('admin.tenants.deactivate_title'),
      message: t('admin.tenants.deactivate_message', { name: detail.name }),
      confirmLabel: t('admin.tenants.deactivate_confirm'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deactivateTenant(detail.id);
          toast.success(t('admin.tenants.deactivated'));
          await load();
        } catch (err: any) {
          toast.error(err?.response?.data?.detail || 'Failed');
        }
      },
    });
  };

  const handleReactivate = async () => {
    if (!detail) return;
    try {
      await reactivateTenant(detail.id);
      toast.success(t('admin.tenants.reactivated'));
      await load();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed');
    }
  };

  const handleHardDelete = async () => {
    if (!detail) return;
    if (deleteConfirm !== detail.name) {
      toast.error(t('admin.tenants.delete_name_mismatch'));
      return;
    }
    setDeleting(true);
    try {
      await hardDeleteTenant(detail.id, deleteConfirm);
      toast.success(t('admin.tenants.deleted'));
      navigate('/admin/system/tenants', { replace: true });
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Delete failed');
    } finally {
      setDeleting(false);
    }
  };

  if (isLoading || !detail) {
    return <LoadingState variant="section" message={t('admin.tenants.loading_detail')} />;
  }

  const tabs: { id: Tab; label: string; icon: any }[] = [
    { id: 'overview', label: t('admin.tenants.tab_overview'), icon: Globe },
    { id: 'users', label: t('admin.tenants.tab_users'), icon: Users },
    { id: 'audit', label: t('admin.tenants.tab_audit'), icon: ShieldCheck },
    { id: 'settings', label: t('admin.tenants.tab_settings'), icon: Database },
  ];

  return (
    <div className="space-y-6 pb-20">
      <PageHeader
        title={detail.name}
        subtitle={detail.slug}
        icon={<Globe className="w-8 h-8" />}
        breadcrumbs={[
          { label: t('admin.tenants.title'), path: '/admin/system/tenants' },
          { label: detail.name },
        ]}
        actions={
          <div className="flex items-center gap-2">
            <button
              onClick={() => navigate('/admin/system/tenants')}
              className="flex items-center gap-1.5 px-3 py-2 text-xs font-bold rounded-lg border border-gray-200 dark:border-dark-border hover:bg-gray-50 dark:hover:bg-dark-border"
            >
              <ArrowLeft className="w-3.5 h-3.5" />
              <span>{t('admin.tenants.back')}</span>
            </button>
            <button
              onClick={handleSwitch}
              disabled={switching || !detail.is_active}
              className="flex items-center gap-1.5 px-4 py-2 text-xs font-bold bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
              title={!detail.is_active ? t('admin.tenants.cannot_switch_inactive') : undefined}
            >
              {switching ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <LogIn className="w-3.5 h-3.5" />}
              <span>{t('admin.tenants.enter_tenant')}</span>
            </button>
          </div>
        }
      />

      {/* Status pill row */}
      <div className="flex items-center gap-2 flex-wrap">
        <span
          className={`px-3 py-1 rounded-full text-[11px] font-black uppercase tracking-widest border ${
            detail.is_active
              ? 'bg-green-50 dark:bg-green-900/20 text-green-600 border-green-100 dark:border-green-800'
              : 'bg-red-50 dark:bg-red-900/20 text-red-600 border-red-100 dark:border-red-800'
          }`}
        >
          {detail.is_active ? t('admin.tenants.status_active') : t('admin.tenants.status_inactive')}
        </span>
        <span className="text-xs text-gray-400 font-mono">{detail.id}</span>
        {detail.owner && (
          <span className="text-xs text-gray-500">
            {t('admin.tenants.owner')}: <strong>{detail.owner.email}</strong>
          </span>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-100 dark:border-dark-border overflow-x-auto">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm font-bold border-b-2 -mb-px transition-colors ${
                isActive
                  ? 'border-indigo-600 text-indigo-600 dark:text-indigo-400'
                  : 'border-transparent text-gray-400 hover:text-gray-700 dark:hover:text-dark-text'
              }`}
            >
              <Icon className="w-4 h-4" />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      {activeTab === 'overview' && (
        <OverviewPanel detail={detail} />
      )}

      {activeTab === 'users' && (
        <UsersPanel tenantId={detail.id} />
      )}

      {activeTab === 'audit' && (
        <AuditPanel tenantId={detail.id} />
      )}

      {activeTab === 'settings' && (
        <form onSubmit={handleSaveSettings} className="bg-white dark:bg-dark-surface p-6 rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border space-y-5">
          <div>
            <label className="block text-xs font-black text-gray-400 uppercase tracking-widest mb-2">
              {t('admin.tenants.field_name')}
            </label>
            <input
              type="text"
              required
              className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl focus:ring-4 focus:ring-indigo-500/10 outline-none dark:text-dark-text font-bold"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </div>
          <div>
            <label className="block text-xs font-black text-gray-400 uppercase tracking-widest mb-2">
              {t('admin.tenants.field_slug')}
            </label>
            <input
              type="text"
              className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl focus:ring-4 focus:ring-indigo-500/10 outline-none dark:text-dark-text font-mono text-sm"
              value={form.slug}
              onChange={(e) => setForm({ ...form, slug: e.target.value })}
            />
          </div>
          <div>
            <label className="block text-xs font-black text-gray-400 uppercase tracking-widest mb-2">
              {t('admin.tenants.field_description')}
            </label>
            <textarea
              rows={2}
              className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl focus:ring-4 focus:ring-indigo-500/10 outline-none dark:text-dark-text text-sm"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </div>
          <div>
            <label className="block text-xs font-black text-gray-400 uppercase tracking-widest mb-2">
              {t('admin.tenants.field_settings')}
            </label>
            <textarea
              rows={8}
              className="w-full px-4 py-3 bg-gray-900 text-green-400 border border-gray-800 rounded-xl focus:ring-4 focus:ring-indigo-500/10 outline-none font-mono text-sm"
              value={form.settings}
              onChange={(e) => setForm({ ...form, settings: e.target.value })}
              spellCheck={false}
            />
          </div>

          <div className="flex flex-wrap gap-2 pt-4 border-t border-gray-100 dark:border-dark-border">
            <button
              type="submit"
              disabled={savingSettings}
              className="flex items-center gap-2 px-5 py-2.5 bg-indigo-600 text-white rounded-xl hover:bg-indigo-700 font-bold text-sm disabled:opacity-60"
            >
              {savingSettings ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              {t('admin.tenants.save')}
            </button>
            <div className="flex-1" />
            {detail.is_active ? (
              <button
                type="button"
                onClick={handleDeactivate}
                className="flex items-center gap-2 px-4 py-2.5 border border-amber-200 text-amber-700 dark:text-amber-400 dark:border-amber-900/50 rounded-xl hover:bg-amber-50 dark:hover:bg-amber-900/20 font-bold text-sm"
              >
                <PowerOff className="w-4 h-4" />
                {t('admin.tenants.deactivate')}
              </button>
            ) : (
              <button
                type="button"
                onClick={handleReactivate}
                className="flex items-center gap-2 px-4 py-2.5 border border-green-200 text-green-700 dark:text-green-400 dark:border-green-900/50 rounded-xl hover:bg-green-50 dark:hover:bg-green-900/20 font-bold text-sm"
              >
                <Power className="w-4 h-4" />
                {t('admin.tenants.reactivate')}
              </button>
            )}
            <button
              type="button"
              onClick={() => { setDeleteConfirm(''); setDeleteModalOpen(true); }}
              className="flex items-center gap-2 px-4 py-2.5 border border-red-200 text-red-700 dark:text-red-400 dark:border-red-900/50 rounded-xl hover:bg-red-50 dark:hover:bg-red-900/20 font-bold text-sm"
            >
              <Trash2 className="w-4 h-4" />
              {t('admin.tenants.delete_permanently')}
            </button>
          </div>
        </form>
      )}

      {/* Hard-delete modal */}
      {deleteModalOpen && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-[1000] p-4">
          <div className="bg-white dark:bg-dark-surface rounded-3xl w-full max-w-lg shadow-2xl overflow-hidden">
            <div className="px-6 py-4 bg-red-50 dark:bg-red-900/20 border-b border-red-100 dark:border-red-900/50 flex justify-between items-center">
              <h2 className="text-lg font-black text-red-700 dark:text-red-400 uppercase tracking-tight">
                {t('admin.tenants.delete_title')}
              </h2>
              <button onClick={() => setDeleteModalOpen(false)} className="p-2 hover:bg-red-100 dark:hover:bg-red-900/40 rounded-full">
                <X className="w-5 h-5 text-red-700 dark:text-red-400" />
              </button>
            </div>
            <div className="p-6 space-y-4">
              <p className="text-sm text-gray-700 dark:text-dark-text">
                {t('admin.tenants.delete_warning', { name: detail.name })}
              </p>
              <p className="text-xs text-gray-500">
                {t('admin.tenants.delete_type_to_confirm')}
              </p>
              <input
                type="text"
                autoFocus
                placeholder={detail.name}
                value={deleteConfirm}
                onChange={(e) => setDeleteConfirm(e.target.value)}
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl focus:ring-4 focus:ring-red-500/10 outline-none dark:text-dark-text font-mono"
              />
              <div className="flex gap-3 pt-2">
                <button
                  onClick={() => setDeleteModalOpen(false)}
                  className="flex-1 px-4 py-3 border border-gray-100 dark:border-dark-border rounded-xl font-bold text-xs uppercase tracking-widest text-gray-500 hover:bg-gray-50 dark:hover:bg-dark-border"
                >
                  {t('admin.tenants.cancel')}
                </button>
                <button
                  onClick={handleHardDelete}
                  disabled={deleting || deleteConfirm !== detail.name}
                  className="flex-[2] px-4 py-3 bg-red-600 text-white rounded-xl font-bold text-xs uppercase tracking-widest hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {deleting ? <Loader2 className="w-4 h-4 animate-spin mx-auto" /> : t('admin.tenants.delete_confirm')}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------- Overview ----------

function OverviewPanel({ detail }: { detail: TenantDetail }) {
  const { t } = useTranslation();
  const stats = detail.stats;
  const cards = [
    { label: t('admin.tenants.stat_users'), value: stats.users_count, sub: `${stats.active_users_count} active`, icon: Users, color: 'indigo' },
    { label: t('admin.tenants.stat_patients'), value: stats.patients_count, icon: Activity, color: 'blue' },
    { label: t('admin.tenants.stat_organizations'), value: stats.organizations_count, icon: Globe, color: 'green' },
    { label: t('admin.tenants.stat_examinations'), value: stats.examinations_count, icon: FileText, color: 'purple' },
    { label: t('admin.tenants.stat_observations'), value: stats.observations_count, icon: Activity, color: 'pink' },
    { label: t('admin.tenants.stat_documents'), value: stats.documents_count, icon: Database, color: 'amber' },
  ];

  return (
    <div className="space-y-6">
      {detail.description && (
        <div className="bg-white dark:bg-dark-surface p-5 rounded-2xl border border-gray-100 dark:border-dark-border">
          <p className="text-xs font-black text-gray-400 uppercase tracking-widest mb-2">
            {t('admin.tenants.field_description')}
          </p>
          <p className="text-sm text-gray-700 dark:text-dark-text">{detail.description}</p>
        </div>
      )}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
        {cards.map((c) => {
          const Icon = c.icon;
          return (
            <div
              key={c.label}
              className="bg-white dark:bg-dark-surface p-5 rounded-2xl border border-gray-100 dark:border-dark-border shadow-sm"
            >
              <div className="flex items-center justify-between mb-2">
                <Icon className={`w-5 h-5 text-${c.color}-500`} />
                {c.sub && <span className="text-[10px] text-gray-400 font-bold">{c.sub}</span>}
              </div>
              <p className="text-2xl font-black text-brand-navy dark:text-dark-text">{c.value.toLocaleString()}</p>
              <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest mt-1">{c.label}</p>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------- Users ----------

function UsersPanel({ tenantId }: { tenantId: string }) {
  const { t } = useTranslation();
  const [users, setUsers] = useState<TenantUser[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteForm, setInviteForm] = useState({ email: '', role: 'USER' as 'USER' | 'MANAGER' | 'ADMIN', expires_days: 7 });
  const [inviteResult, setInviteResult] = useState<string | null>(null);
  const [issuing, setIssuing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data: TenantUserListResponse = await listTenantUsers(tenantId, { limit: 100 });
      setUsers(data.items);
      setTotal(data.total);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed');
    } finally {
      setLoading(false);
    }
  }, [tenantId]);

  useEffect(() => { load(); }, [load]);

  const handleRoleChange = async (user: TenantUser, role: 'USER' | 'MANAGER' | 'ADMIN') => {
    try {
      await updateTenantUser(tenantId, user.id, { role });
      toast.success(t('admin.tenants.role_updated'));
      await load();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed');
    }
  };

  const handleActiveToggle = async (user: TenantUser) => {
    try {
      await updateTenantUser(tenantId, user.id, { is_active: !user.is_active });
      await load();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed');
    }
  };

  const handleInvite = async (e: React.FormEvent) => {
    e.preventDefault();
    setIssuing(true);
    try {
      const result = await createTenantInvite(tenantId, {
        email: inviteForm.email || undefined,
        role: inviteForm.role,
        expires_days: inviteForm.expires_days,
      });
      setInviteResult(result.invite_token);
      toast.success('Invite token issued');
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed');
    } finally {
      setIssuing(false);
    }
  };

  if (loading) return <LoadingState variant="section" message="Loading users..." />;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-gray-400">{total} {t('admin.tenants.users_total')}</p>
        <button
          onClick={() => { setInviteOpen(true); setInviteResult(null); }}
          className="flex items-center gap-1.5 px-3 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 text-xs font-bold"
        >
          <ShieldCheck className="w-3.5 h-3.5" />
          {t('admin.tenants.issue_invite')}
        </button>
      </div>

      <div className="bg-white dark:bg-dark-surface rounded-2xl border border-gray-100 dark:border-dark-border overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="bg-gray-50 dark:bg-dark-bg text-left text-[10px] font-black text-gray-400 uppercase tracking-widest">
              <th className="px-4 py-3">{t('admin.tenants.col_email')}</th>
              <th className="px-4 py-3">{t('admin.tenants.col_role')}</th>
              <th className="px-4 py-3">{t('admin.tenants.col_status')}</th>
              <th className="px-4 py-3">{t('admin.tenants.col_created')}</th>
              <th className="px-4 py-3 text-right">{t('admin.tenants.col_actions')}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50 dark:divide-dark-border">
            {users.map((u) => (
              <tr key={u.id} className="hover:bg-gray-50 dark:hover:bg-dark-bg/50">
                <td className="px-4 py-3 text-sm font-medium text-brand-navy dark:text-dark-text">{u.email}</td>
                <td className="px-4 py-3">
                  <select
                    value={u.role}
                    onChange={(e) => handleRoleChange(u, e.target.value as any)}
                    className="text-xs px-2 py-1 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-md font-bold"
                  >
                    <option value="USER">USER</option>
                    <option value="MANAGER">MANAGER</option>
                    <option value="ADMIN">ADMIN</option>
                  </select>
                </td>
                <td className="px-4 py-3">
                  <button
                    onClick={() => handleActiveToggle(u)}
                    className={`px-2 py-0.5 rounded-md text-[10px] font-black uppercase border ${
                      u.is_active
                        ? 'bg-green-50 dark:bg-green-900/20 text-green-600 border-green-100 dark:border-green-800'
                        : 'bg-gray-100 dark:bg-dark-border text-gray-500 border-gray-200 dark:border-dark-border'
                    }`}
                  >
                    {u.is_active ? 'Active' : 'Disabled'}
                  </button>
                </td>
                <td className="px-4 py-3 text-xs text-gray-400">
                  {u.created_at ? new Date(u.created_at).toLocaleDateString() : '—'}
                </td>
                <td className="px-4 py-3" />
              </tr>
            ))}
            {users.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-sm text-gray-400">
                  {t('admin.tenants.no_users')}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {inviteOpen && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-[1000] p-4">
          <div className="bg-white dark:bg-dark-surface rounded-3xl w-full max-w-lg shadow-2xl overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-100 dark:border-dark-border flex justify-between items-center">
              <h2 className="text-lg font-black uppercase tracking-tight">{t('admin.tenants.issue_invite')}</h2>
              <button onClick={() => setInviteOpen(false)} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-border rounded-full">
                <X className="w-5 h-5" />
              </button>
            </div>
            <form onSubmit={handleInvite} className="p-6 space-y-4">
              <div>
                <label className="block text-xs font-black text-gray-400 uppercase tracking-widest mb-2">Email (optional)</label>
                <input
                  type="email"
                  className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl dark:text-dark-text"
                  value={inviteForm.email}
                  onChange={(e) => setInviteForm({ ...inviteForm, email: e.target.value })}
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-black text-gray-400 uppercase tracking-widest mb-2">Role</label>
                  <select
                    className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl dark:text-dark-text font-bold"
                    value={inviteForm.role}
                    onChange={(e) => setInviteForm({ ...inviteForm, role: e.target.value as any })}
                  >
                    <option value="USER">USER</option>
                    <option value="MANAGER">MANAGER</option>
                    <option value="ADMIN">ADMIN</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-black text-gray-400 uppercase tracking-widest mb-2">Expires (days)</label>
                  <input
                    type="number"
                    min={1}
                    max={30}
                    className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl dark:text-dark-text"
                    value={inviteForm.expires_days}
                    onChange={(e) => setInviteForm({ ...inviteForm, expires_days: Number(e.target.value) })}
                  />
                </div>
              </div>
              {inviteResult && (
                <div className="bg-gray-900 text-green-400 p-3 rounded-xl font-mono text-xs break-all relative">
                  <button
                    type="button"
                    onClick={() => navigator.clipboard.writeText(inviteResult)}
                    className="absolute top-2 right-2 p-1 hover:bg-white/10 rounded"
                    title="Copy"
                  >
                    <Copy className="w-3.5 h-3.5" />
                  </button>
                  {inviteResult}
                </div>
              )}
              <button
                type="submit"
                disabled={issuing}
                className="w-full px-4 py-3 bg-indigo-600 text-white rounded-xl font-bold text-sm hover:bg-indigo-700 disabled:opacity-60 flex items-center justify-center gap-2"
              >
                {issuing && <Loader2 className="w-4 h-4 animate-spin" />}
                {inviteResult ? 'Re-issue' : 'Issue Invite'}
              </button>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------- Audit ----------

function AuditPanel({ tenantId }: { tenantId: string }) {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [actionFilter, setActionFilter] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data: AuditListResponse = await listTenantAudit(tenantId, { limit: 50, action: actionFilter || undefined });
      setEntries(data.items);
      setTotal(data.total);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed');
    } finally {
      setLoading(false);
    }
  }, [tenantId, actionFilter]);

  useEffect(() => { load(); }, [load]);

  if (loading) return <LoadingState variant="section" message="Loading audit log..." />;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <input
          type="text"
          placeholder="Filter by action (e.g. tenant.update)"
          value={actionFilter}
          onChange={(e) => setActionFilter(e.target.value)}
          className="px-3 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-lg text-xs dark:text-dark-text w-64"
        />
        <span className="text-xs text-gray-400">{total} entries</span>
      </div>
      <div className="bg-white dark:bg-dark-surface rounded-2xl border border-gray-100 dark:border-dark-border overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="bg-gray-50 dark:bg-dark-bg text-left text-[10px] font-black text-gray-400 uppercase tracking-widest">
              <th className="px-4 py-3">When</th>
              <th className="px-4 py-3">Action</th>
              <th className="px-4 py-3">Resource</th>
              <th className="px-4 py-3">Diff</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50 dark:divide-dark-border">
            {entries.map((e) => (
              <tr key={e.id} className="align-top hover:bg-gray-50 dark:hover:bg-dark-bg/50">
                <td className="px-4 py-3 text-xs text-gray-400 whitespace-nowrap">
                  {e.created_at ? new Date(e.created_at).toLocaleString() : '—'}
                </td>
                <td className="px-4 py-3 text-xs font-mono font-bold text-indigo-600 dark:text-indigo-400">{e.action}</td>
                <td className="px-4 py-3 text-xs text-gray-500">{e.resource_type}{e.resource_id ? ` / ${e.resource_id.slice(0, 8)}…` : ''}</td>
                <td className="px-4 py-3 text-[10px] text-gray-400 font-mono max-w-md">
                  {(e.old_value || e.new_value) && (
                    <pre className="whitespace-pre-wrap break-all max-h-20 overflow-y-auto">
                      {e.old_value ? `- ${JSON.stringify(e.old_value).slice(0, 200)}\n` : ''}
                      {e.new_value ? `+ ${JSON.stringify(e.new_value).slice(0, 200)}` : ''}
                    </pre>
                  )}
                </td>
              </tr>
            ))}
            {entries.length === 0 && (
              <tr><td colSpan={4} className="px-4 py-8 text-center text-sm text-gray-400">No audit entries</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default TenantDetail;
