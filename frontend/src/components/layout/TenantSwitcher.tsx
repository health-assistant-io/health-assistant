import { useState, useRef, useEffect } from 'react';
import { Building2, ChevronDown, Check, Search, ArrowLeft, Users, ShieldCheck } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import { useNavigate } from 'react-router-dom';
import { useTenantStore } from '../../store/slices/tenantSlice';
import { useAuthStore } from '../../store/slices/authSlice';
import { useTenantSwitchStore, performTenantSwitch } from '../../store/slices/tenantSwitchSlice';
import { usePatientStore } from '../../store/slices/patientSlice';
import type { Tenant } from '../../services/tenantService';

interface Props {
  className?: string;
}

export const TenantSwitcher: React.FC<Props> = ({ className = '' }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { user, login } = useAuthStore();
  const { currentTenant, tenants, loadTenants, setCurrentTenant, isLoadingList } = useTenantStore();
  const { switched, scopedTenant, exitTenant } = useTenantSwitchStore();
  const clearPatientContext = usePatientStore((s) => s.clearPatientContext);

  const [isOpen, setIsOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [switching, setSwitching] = useState<string | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const isSystemAdmin = user?.role === 'SYSTEM_ADMIN';
  // SYSTEM_ADMIN can always switch (even when already in a switched
  // session — the handleSelect function exits the current switch first).
  const canSwitch = isSystemAdmin;

  const activeTenant = switched ? scopedTenant : currentTenant;
  // A SYSTEM_ADMIN is a platform-level role, not a tenant member.
  // When not switched, they're in a "global" state — show "System Admin"
  // instead of the home tenant name they don't identify with.
  const activeName = switched
    ? (activeTenant?.name ?? '—')
    : (isSystemAdmin ? t('common.system_admin', { defaultValue: 'System Admin' }) : (activeTenant?.name ?? '—'));

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    if (isOpen) document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen]);

  // Lazy-load tenant list when the dropdown is opened by an admin
  useEffect(() => {
    if (isOpen && canSwitch && tenants.length === 0 && !isLoadingList) {
      loadTenants();
    }
  }, [isOpen, canSwitch, tenants.length, isLoadingList, loadTenants]);

  const filteredTenants = tenants.filter((t) => {
    const q = searchTerm.toLowerCase();
    return (
      t.name.toLowerCase().includes(q) ||
      t.slug.toLowerCase().includes(q) ||
      (t.description ?? '').toLowerCase().includes(q)
    );
  });

  const handleSelect = async (tenant: Tenant) => {
    if (tenant.id === activeTenant?.id && !switched) {
      setIsOpen(false);
      return;
    }
    setSwitching(tenant.id);
    try {
      // If already in a switched session, exit first (the backend rejects
      // a second switch without exiting). Restore original tokens so the
      // next switch call is made with the original (non-switched) JWT.
      if (switched) {
        await exitTenant();
        const originalAccess = localStorage.getItem('originalAccessToken');
        const originalRefresh = localStorage.getItem('originalRefreshToken');
        if (originalAccess && originalRefresh) {
          login(originalAccess, originalRefresh);
        }
      }

      await performTenantSwitch(tenant.id, (access, refresh) => {
        login(access, refresh);
      });
      setCurrentTenant(tenant);
      clearPatientContext();
      toast.success(
        t('common.tenant_switched_to', { defaultValue: 'Switched to {{name}}', name: tenant.name }),
      );
      setIsOpen(false);
      setSearchTerm('');
      navigate('/', { replace: true });
    } catch (err) {
      console.error('Tenant switch failed:', err);
      toast.error(t('common.tenant_switch_failed', { defaultValue: 'Failed to switch tenant' }));
    } finally {
      setSwitching(null);
    }
  };

  const handleExitSwitch = async () => {
    const originalAccess = localStorage.getItem('originalAccessToken');
    const originalRefresh = localStorage.getItem('originalRefreshToken');
    await exitTenant();
    if (originalAccess && originalRefresh) {
      login(originalAccess, originalRefresh);
    }
    clearPatientContext();
    setIsOpen(false);
    navigate('/', { replace: true });
    toast.success(t('common.tenant_exited', { defaultValue: 'Exited tenant view' }));
  };

  // ── Non-admin: static badge (informative, no dropdown) ──────────────
  if (!isSystemAdmin) {
    return (
      <div
        className={`flex items-center gap-2 px-3 h-9 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl text-gray-500 dark:text-dark-muted ${className}`}
        title={activeTenant?.description ?? activeName}
      >
        <Building2 className="w-4 h-4 text-gray-400 flex-shrink-0" />
        <span className="text-xs font-bold truncate max-w-[140px]">{activeName}</span>
      </div>
    );
  }

  // ── SYSTEM_ADMIN (switched or not): interactive dropdown ────────────
  return (
    <div className={`relative ${className}`} ref={dropdownRef}>
      <div
        className={`flex items-center gap-2 px-3 h-9 rounded-xl border cursor-pointer transition-all duration-200 active:scale-95
          ${switched
            ? 'bg-amber-50 dark:bg-amber-900/20 border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-400 hover:border-amber-300 dark:hover:border-amber-700'
            : 'bg-gray-50 dark:bg-dark-bg border-gray-100 dark:border-dark-border text-gray-500 dark:text-dark-muted hover:border-gray-200 dark:hover:border-dark-border-strong'
          }`}
        onClick={() => setIsOpen(!isOpen)}
      >
        <Building2 className={`w-4 h-4 flex-shrink-0 ${switched ? 'text-amber-500' : 'text-gray-400'}`} />
        <div className="flex flex-col min-w-0 leading-tight">
          <span className="text-xs font-bold truncate max-w-[120px]">{activeName}</span>
          {switched ? (
            <span className="text-[8px] font-black uppercase tracking-widest text-amber-500 opacity-80">
              {t('common.switched', { defaultValue: 'Switched' })}
            </span>
          ) : isSystemAdmin ? (
            <span className="text-[8px] font-black uppercase tracking-widest text-gray-400 opacity-60">
              {t('common.global_view', { defaultValue: 'Global View' })}
            </span>
          ) : null}
        </div>
        <ChevronDown className={`w-3.5 h-3.5 transition-transform flex-shrink-0 ${isOpen ? 'rotate-180' : ''} ${switched ? 'text-amber-400' : 'text-gray-400'}`} />
      </div>

      {isOpen && (
        <div className="absolute right-0 z-[600] w-80 mt-2 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-2xl shadow-2xl overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200">
          {/* Search header */}
          <div className="p-3 border-b border-gray-50 dark:border-dark-border sticky top-0 bg-white/95 dark:bg-dark-surface/95 backdrop-blur-md z-10">
            <div className="flex items-center justify-between mb-2 px-1">
              <span className="text-[10px] font-black text-gray-400 uppercase tracking-widest">
                {t('common.tenants', { defaultValue: 'Tenants' })}
              </span>
              <span className="text-[10px] font-bold text-blue-500 bg-blue-50 dark:bg-blue-900/30 px-2 py-0.5 rounded-full">
                {tenants.length}
              </span>
            </div>
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
              <input
                type="text"
                autoFocus
                placeholder={t('common.search_tenants', { defaultValue: 'Search tenants...' })}
                className="w-full pl-9 pr-4 py-2 bg-gray-50/80 dark:bg-dark-bg/50 border border-gray-100 dark:border-dark-border rounded-xl text-xs outline-none focus:ring-2 focus:ring-blue-500/20 dark:text-dark-text transition-all"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
            </div>
          </div>

          {/* Exit-switch bar (only when currently switched) */}
          {switched && (
            <div className="px-3 py-2 bg-amber-50/50 dark:bg-amber-900/10 border-b border-amber-100 dark:border-amber-800/30">
              <button
                onClick={handleExitSwitch}
                className="w-full flex items-center justify-center gap-2 py-2 px-3 rounded-xl text-[11px] font-black uppercase tracking-widest text-amber-700 dark:text-amber-400 bg-white dark:bg-dark-surface hover:bg-amber-50 dark:hover:bg-amber-900/20 transition-all border border-amber-200 dark:border-amber-800/50"
              >
                <ArrowLeft className="w-3.5 h-3.5" />
                <span>{t('common.exit_tenant_switch', { defaultValue: 'Exit Tenant View' })}</span>
              </button>
            </div>
          )}

          {/* Tenant list */}
          <div className="max-h-72 overflow-y-auto custom-scrollbar p-1">
            {isLoadingList && tenants.length === 0 ? (
              <div className="px-4 py-10 text-xs text-gray-400 italic text-center">
                <Building2 className="w-10 h-10 mx-auto mb-3 opacity-10" />
                <p className="font-bold uppercase tracking-widest text-[10px] opacity-60">
                  {t('common.loading', { defaultValue: 'Loading...' })}
                </p>
              </div>
            ) : filteredTenants.length > 0 ? (
              filteredTenants.map((tenant) => {
                const isActive = tenant.id === activeTenant?.id;
                const isDisabled = !tenant.is_active || switching === tenant.id;
                return (
                  <div
                    key={tenant.id}
                    className={`group px-3 py-2.5 text-sm flex items-center justify-between cursor-pointer hover:bg-blue-50/50 dark:hover:bg-blue-900/20 rounded-xl transition-all mb-0.5
                      ${isActive ? 'bg-blue-50 dark:bg-blue-900/30' : ''} ${isDisabled ? 'opacity-40 pointer-events-none' : ''}`}
                    onClick={() => !isDisabled && handleSelect(tenant)}
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <div className={`w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 transition-all
                        ${isActive
                          ? 'bg-blue-600 text-white shadow-lg shadow-blue-500/20'
                          : 'bg-gray-100 text-gray-400 dark:bg-dark-bg group-hover:bg-blue-100 group-hover:text-blue-500'}`}>
                        <Building2 className="w-4 h-4" />
                      </div>
                      <div className="flex flex-col min-w-0">
                        <span className={`text-xs font-bold truncate ${isActive ? 'text-blue-700 dark:text-blue-300' : 'text-gray-700 dark:text-dark-text'}`}>
                          {tenant.name}
                        </span>
                        <div className="flex items-center gap-2 mt-0.5">
                          <span className="text-[9px] text-gray-400 font-bold uppercase tracking-tighter opacity-70">
                            {tenant.slug}
                          </span>
                          {!tenant.is_active && (
                            <span className="text-[8px] font-black uppercase text-red-400 bg-red-50 dark:bg-red-900/20 px-1.5 py-0.5 rounded-full">
                              {t('common.inactive', { defaultValue: 'Inactive' })}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                    {isActive && <Check className="w-4 h-4 text-blue-600 dark:text-blue-400 flex-shrink-0" />}
                    {switching === tenant.id && (
                      <span className="text-[10px] text-blue-500 font-bold animate-pulse flex-shrink-0">
                        {t('common.switching', { defaultValue: 'Switching...' })}
                      </span>
                    )}
                  </div>
                );
              })
            ) : (
              <div className="px-4 py-10 text-xs text-gray-400 italic text-center">
                <Users className="w-10 h-10 mx-auto mb-3 opacity-10" />
                <p className="font-bold uppercase tracking-widest text-[10px] opacity-60">
                  {searchTerm
                    ? t('common.no_results', { defaultValue: 'No results' })
                    : t('common.no_tenants', { defaultValue: 'No tenants found' })}
                </p>
              </div>
            )}
          </div>

          {/* Footer link to admin */}
          <div className="p-2 border-t border-gray-50 dark:border-dark-border bg-gray-50/30 dark:bg-dark-bg/20">
            <button
              className="w-full py-2.5 px-3 rounded-xl text-[10px] font-black uppercase tracking-widest text-blue-600 hover:bg-blue-50 dark:text-blue-400 dark:hover:bg-blue-900/30 transition-all text-center flex items-center justify-center space-x-2"
              onClick={() => {
                navigate('/admin/system/tenants');
                setIsOpen(false);
              }}
            >
              <Building2 className="w-3.5 h-3.5" />
              <span>{t('admin.tenant_management', { defaultValue: 'Tenant Management' })}</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
