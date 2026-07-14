import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Plus, Settings, X, Save, Globe, Database, Building2,
  Calendar, ChevronRight, Search, Power, PowerOff,
} from 'lucide-react';import { toast } from 'react-toastify';
import {
  listTenants, createTenant, deactivateTenant, reactivateTenant,
  type Tenant, type TenantListResponse,
} from '../../services/tenantService';
import { useUIStore } from '../../store/slices/uiSlice';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { useTranslation } from 'react-i18next';

const PAGE_SIZE = 25;

function TenantManagement() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const showConfirmation = useUIStore((s) => s.showConfirmation);

  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formData, setFormData] = useState({ name: '', slug: '', description: '', settings: '{}' });

  const fetchTenants = useCallback(async () => {
    try {
      const data: TenantListResponse = await listTenants({
        search: search || undefined,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      });
      setTenants(data.items);
      setTotal(data.total);
    } catch (err) {
      console.error('Failed to fetch tenants:', err);
      toast.error(t('admin.tenants.fetch_failed'));
    }
  }, [search, page, t]);

  useEffect(() => {
    const loadData = async () => {
      setIsLoading(true);
      await fetchTenants();
      setIsLoading(false);
    };
    loadData();
  }, [fetchTenants]);

  const handleSearch = (value: string) => {
    setSearch(value);
    setPage(0);
  };

  const openCreate = () => {
    setFormData({ name: '', slug: '', description: '', settings: '{}' });
    setIsModalOpen(true);
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    let settingsObj: Record<string, any> = {};
    try {
      settingsObj = formData.settings.trim() ? JSON.parse(formData.settings) : {};
    } catch {
      toast.error('Invalid settings JSON');
      return;
    }
    setSaving(true);
    try {
      await createTenant({
        name: formData.name,
        slug: formData.slug || undefined,
        description: formData.description || undefined,
        settings: settingsObj,
      });
      toast.success(t('admin.tenants.create_success'));
      setIsModalOpen(false);
      await fetchTenants();
    } catch (err: any) {
      const detail = err?.response?.data?.detail || 'Failed to create tenant';
      toast.error(detail);
    } finally {
      setSaving(false);
    }
  };

  const handleDeactivate = (tenant: Tenant) => {
    showConfirmation({
      title: t('admin.tenants.deactivate_title'),
      message: t('admin.tenants.deactivate_message', { name: tenant.name }),
      confirmLabel: t('admin.tenants.deactivate_confirm'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deactivateTenant(tenant.id);
          toast.success(t('admin.tenants.deactivated'));
          await fetchTenants();
        } catch (err: any) {
          toast.error(err?.response?.data?.detail || 'Failed');
        }
      },
    });
  };

  const handleReactivate = async (tenant: Tenant) => {
    try {
      await reactivateTenant(tenant.id);
      toast.success(t('admin.tenants.reactivated'));
      await fetchTenants();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed');
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500">
        {t('admin.tenants.loading')}
      </div>
    );
  }

  return (
    <div className="space-y-6 pb-20">
      <PageHeader
        title={t('admin.tenants.title')}
        subtitle={t('admin.tenants.subtitle')}
        icon={<Globe className="w-8 h-8" />}
      />

      <StickyToolbar
        actions={
          <div className="flex items-center gap-2">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                type="text"
                placeholder={t('admin.tenants.search_placeholder')}
                value={search}
                onChange={(e) => handleSearch(e.target.value)}
                className="pl-9 pr-3 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl text-sm focus:ring-2 focus:ring-indigo-500/20 outline-none dark:text-dark-text w-64"
              />
            </div>
            <button
              onClick={openCreate}
              className="flex items-center space-x-2 px-5 py-2.5 bg-indigo-600 text-white rounded-xl hover:bg-indigo-700 transition-all shadow-lg shadow-indigo-200/50 dark:shadow-none font-bold active:scale-95"
            >
              <Plus className="w-4 h-4" />
              <span>{t('admin.tenants.new')}</span>
            </button>
          </div>
        }
      />

      {tenants.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          <Database className="w-12 h-12 mx-auto mb-3 opacity-50" />
          <p className="font-medium">{t('admin.tenants.empty')}</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
          {tenants.map((tenant) => (
            <div
              key={tenant.id}
              className={`bg-white dark:bg-dark-surface p-6 rounded-2xl shadow-sm border ${
                tenant.is_active
                  ? 'border-gray-100 dark:border-dark-border'
                  : 'border-red-200 dark:border-red-900/50 opacity-75'
              } group relative hover:shadow-xl transition-all duration-300`}
            >
              <div className="flex justify-between items-start mb-4">
                <button
                  onClick={() => navigate(`/admin/system/tenants/${tenant.id}`)}
                  className="flex items-center space-x-3 text-left min-w-0"
                >
                  <div className="w-12 h-12 bg-indigo-50 dark:bg-indigo-900/30 rounded-2xl flex items-center justify-center border border-indigo-100 dark:border-indigo-800 shrink-0">
                    <Database className="w-6 h-6 text-indigo-500" />
                  </div>
                  <div className="min-w-0">
                    <h3 className="text-lg font-black text-brand-navy dark:text-dark-text tracking-tight truncate">
                      {tenant.name}
                    </h3>
                    <div className="flex items-center space-x-2 mt-0.5">
                      <span
                        className={`px-2 py-0.5 rounded-md text-[10px] font-black uppercase tracking-widest border ${
                          tenant.is_active
                            ? 'bg-green-50 dark:bg-green-900/20 text-green-600 border-green-100 dark:border-green-800'
                            : 'bg-red-50 dark:bg-red-900/20 text-red-600 border-red-100 dark:border-red-800'
                        }`}
                      >
                        {tenant.is_active ? t('admin.tenants.status_active') : t('admin.tenants.status_inactive')}
                      </span>
                      <span className="text-[10px] text-gray-400 font-mono truncate">{tenant.slug}</span>
                    </div>
                  </div>
                </button>
                <div className="flex items-center space-x-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                  <button
                    onClick={() => navigate(`/admin/system/tenants/${tenant.id}`)}
                    className="p-2 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 rounded-xl transition-all"
                    title={t('admin.tenants.configure')}
                  >
                    <Settings className="w-5 h-5" />
                  </button>
                  {tenant.is_active ? (
                    <button
                      onClick={() => handleDeactivate(tenant)}
                      className="p-2 text-gray-400 hover:text-amber-600 hover:bg-amber-50 dark:hover:bg-amber-900/20 rounded-xl transition-all"
                      title={t('admin.tenants.deactivate')}
                    >
                      <PowerOff className="w-5 h-5" />
                    </button>
                  ) : (
                    <button
                      onClick={() => handleReactivate(tenant)}
                      className="p-2 text-gray-400 hover:text-green-600 hover:bg-green-50 dark:hover:bg-green-900/20 rounded-xl transition-all"
                      title={t('admin.tenants.reactivate')}
                    >
                      <Power className="w-5 h-5" />
                    </button>
                  )}
                </div>
              </div>

              {tenant.description && (
                <p className="text-sm text-gray-500 dark:text-dark-muted mb-3 line-clamp-2">
                  {tenant.description}
                </p>
              )}

              <div className="grid grid-cols-2 gap-3 py-3 border-y border-gray-50 dark:border-dark-border text-xs">
                <div className="space-y-0.5">
                  <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest flex items-center">
                    <Building2 className="w-3 h-3 mr-1" /> {t('admin.tenants.created')}
                  </p>
                  <p className="font-bold text-brand-navy dark:text-dark-text">
                    {tenant.created_at ? new Date(tenant.created_at).toLocaleDateString() : '—'}
                  </p>
                </div>
                <div className="space-y-0.5">
                  <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest flex items-center">
                    <Calendar className="w-3 h-3 mr-1" /> {t('admin.tenants.updated')}
                  </p>
                  <p className="font-bold text-brand-navy dark:text-dark-text">
                    {tenant.updated_at ? new Date(tenant.updated_at).toLocaleDateString() : '—'}
                  </p>
                </div>
              </div>

              <button
                onClick={() => navigate(`/admin/system/tenants/${tenant.id}`)}
                className="mt-4 w-full flex items-center justify-between group/link cursor-pointer text-indigo-600 dark:text-indigo-400 hover:underline"
              >
                <span className="text-xs font-black uppercase tracking-tighter">
                  {t('admin.tenants.view_details')}
                </span>
                <div className="w-7 h-7 rounded-full bg-indigo-50 dark:bg-indigo-900/30 flex items-center justify-center">
                  <ChevronRight className="w-4 h-4 group-hover/link:translate-x-1 transition-transform" />
                </div>
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Pagination */}
      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between pt-4">
          <p className="text-xs text-gray-400">
            {t('admin.tenants.pagination', { from: page * PAGE_SIZE + 1, to: Math.min((page + 1) * PAGE_SIZE, total), total })}
          </p>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage(Math.max(0, page - 1))}
              disabled={page === 0}
              className="px-3 py-1.5 text-xs font-bold rounded-lg border border-gray-200 dark:border-dark-border disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-dark-border"
            >
              ← {t('admin.tenants.prev')}
            </button>
            <span className="text-xs text-gray-500">
              {page + 1} / {totalPages}
            </span>
            <button
              onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
              disabled={page >= totalPages - 1}
              className="px-3 py-1.5 text-xs font-bold rounded-lg border border-gray-200 dark:border-dark-border disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-dark-border"
            >
              {t('admin.tenants.next')} →
            </button>
          </div>
        </div>
      )}

      {/* Create modal */}
      {isModalOpen && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-[1000] p-4 animate-in fade-in duration-200">
          <div className="bg-white dark:bg-dark-surface rounded-3xl w-full max-w-2xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200">
            <div className="px-8 py-6 border-b border-gray-100 dark:border-dark-border flex justify-between items-center bg-gray-50/50 dark:bg-dark-bg/50">
              <h2 className="text-xl font-black text-brand-navy dark:text-dark-text uppercase tracking-tight">
                {t('admin.tenants.create_title')}
              </h2>
              <button
                onClick={() => setIsModalOpen(false)}
                className="p-2 hover:bg-gray-100 dark:hover:bg-dark-border rounded-full transition-colors text-gray-400"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <form onSubmit={handleCreate} className="p-8 space-y-5">
              <div>
                <label className="block text-xs font-black text-gray-400 uppercase tracking-widest mb-2">
                  {t('admin.tenants.field_name')} *
                </label>
                <input
                  type="text"
                  required
                  className="w-full px-5 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-2xl focus:ring-4 focus:ring-indigo-500/10 outline-none transition-all dark:text-dark-text font-bold text-lg"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  placeholder="e.g. Northwell Health System"
                />
              </div>
              <div>
                <label className="block text-xs font-black text-gray-400 uppercase tracking-widest mb-2">
                  {t('admin.tenants.field_slug')}
                </label>
                <input
                  type="text"
                  className="w-full px-5 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-2xl focus:ring-4 focus:ring-indigo-500/10 outline-none transition-all dark:text-dark-text font-mono text-sm"
                  value={formData.slug}
                  onChange={(e) => setFormData({ ...formData, slug: e.target.value })}
                  placeholder="auto-generated from name if blank"
                />
              </div>
              <div>
                <label className="block text-xs font-black text-gray-400 uppercase tracking-widest mb-2">
                  {t('admin.tenants.field_description')}
                </label>
                <textarea
                  rows={2}
                  className="w-full px-5 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-2xl focus:ring-4 focus:ring-indigo-500/10 outline-none transition-all dark:text-dark-text text-sm"
                  value={formData.description}
                  onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                />
              </div>
              <div>
                <label className="block text-xs font-black text-gray-400 uppercase tracking-widest mb-2">
                  {t('admin.tenants.field_settings')}
                </label>
                <textarea
                  rows={5}
                  className="w-full px-5 py-4 bg-gray-900 text-green-400 border border-gray-800 rounded-2xl focus:ring-4 focus:ring-indigo-500/10 outline-none transition-all font-mono text-sm leading-relaxed"
                  value={formData.settings}
                  onChange={(e) => setFormData({ ...formData, settings: e.target.value })}
                  spellCheck={false}
                />
              </div>
              <div className="pt-2 flex space-x-4">
                <button
                  type="button"
                  onClick={() => setIsModalOpen(false)}
                  className="flex-1 px-6 py-4 border border-gray-100 dark:border-dark-border rounded-2xl hover:bg-gray-50 dark:hover:bg-dark-border transition-all font-bold text-gray-500 uppercase tracking-widest text-xs"
                >
                  {t('admin.tenants.cancel')}
                </button>
                <button
                  type="submit"
                  disabled={saving}
                  className="flex-[2] px-6 py-4 bg-indigo-600 text-white rounded-2xl hover:bg-indigo-700 transition-all font-bold flex items-center justify-center space-x-2 shadow-xl shadow-indigo-200 dark:shadow-none active:scale-95 disabled:opacity-60"
                >
                  <Save className="w-5 h-5" />
                  <span className="uppercase tracking-widest text-sm">{t('admin.tenants.create')}</span>
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

export default TenantManagement;
