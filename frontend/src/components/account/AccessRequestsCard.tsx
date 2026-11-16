import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { toast } from 'react-toastify';
import { KeyRound, Check, X, Inbox } from 'lucide-react';
import {
  getMyGrants,
  acceptMyGrant,
  declineMyGrant,
  type MeGrant,
} from '../../services/patientAccessService';
import { useAccessStore } from '../../store/slices/accessSlice';
import { apiErrorDetail } from '../../utils/apiErrors';

interface UseMyAccessGrantsResult {
  active: MeGrant[];
  pending: MeGrant[];
  loading: boolean;
  loadError: string | null;
  reload: () => Promise<void>;
  accept: (grant: MeGrant) => Promise<void>;
  decline: (grant: MeGrant) => Promise<void>;
  /** True while an accept/decline move is in flight (buttons disable). */
  acting: boolean;
}

/**
 * The signed-in caller's incoming care-team grants (`GET /me/access/grants`)
 * plus the accept/decline moves. After every move the `/me/access` mirror
 * re-hydrates so `canPatient` and the nav-side grant data stay in step.
 */
export function useMyAccessGrants(): UseMyAccessGrantsResult {
  const { t } = useTranslation();
  const hydrateAccess = useAccessStore((s) => s.hydrate);
  const [active, setActive] = useState<MeGrant[]>([]);
  const [pending, setPending] = useState<MeGrant[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [acting, setActing] = useState(false);

  const reload = useCallback(async () => {
    setLoadError(null);
    try {
      const data = await getMyGrants();
      setActive(data.active);
      setPending(data.pending);
    } catch (err) {
      setLoadError(apiErrorDetail(err) || t('access.load_failed', 'Could not load the access list.'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    reload();
  }, [reload]);

  const move = async (grant: MeGrant, action: 'accept' | 'decline') => {
    setActing(true);
    try {
      if (action === 'accept') {
        await acceptMyGrant(grant.id);
        toast.success(
          t('access.accepted_toast', {
            defaultValue: 'Access to {{patient}} accepted.',
            patient: grant.patient_name ?? '',
          })
        );
      } else {
        await declineMyGrant(grant.id);
        toast.info(t('access.declined_toast', 'Request declined.'));
      }
      await reload();
      await hydrateAccess();
    } catch (err) {
      toast.error(apiErrorDetail(err) || t('access.move_failed', 'Could not update the request.'));
    } finally {
      setActing(false);
    }
  };

  return {
    active,
    pending,
    loading,
    loadError,
    reload,
    acting,
    accept: (grant) => move(grant, 'accept'),
    decline: (grant) => move(grant, 'decline'),
  };
}

export function AccessRequestsCard() {
  const { t } = useTranslation();
  const { active, pending, loading, loadError, accept, decline, acting } = useMyAccessGrants();

  return (
    <div className="bg-white dark:bg-dark-surface rounded-3xl shadow-xs border border-gray-100 dark:border-dark-border overflow-hidden">
      <div className="px-8 py-6 border-b border-gray-100 dark:border-dark-border flex items-center justify-between bg-gray-50/30 dark:bg-dark-bg/20">
        <div className="flex items-center space-x-3">
          <div className="p-2 bg-amber-50 dark:bg-amber-900/20 rounded-xl">
            <KeyRound className="w-5 h-5 text-amber-600" />
          </div>
          <h3 className="text-lg font-bold text-brand-navy dark:text-dark-text tracking-tight">
            {t('access.requests_title', 'Access requests')}
          </h3>
        </div>
        {pending.length > 0 && (
          <span
            data-testid="access-pending-badge"
            className="px-2.5 py-1 rounded-full bg-amber-500 text-white text-[10px] font-black uppercase tracking-widest"
          >
            {pending.length}
          </span>
        )}
      </div>

      <div className="p-8 space-y-6">
        {loadError && (
          <div className="p-4 rounded-2xl bg-red-50 dark:bg-red-900/20 text-sm text-red-700 dark:text-red-300">
            {loadError}
          </div>
        )}

        {loading && !loadError && (
          <p className="text-sm text-gray-400">{t('common.loading', 'Loading…')}</p>
        )}

        {!loading && !loadError && pending.length === 0 && active.length === 0 && (
          <div className="p-6 bg-gray-50 dark:bg-dark-bg/50 rounded-2xl border border-dashed border-gray-200 dark:border-dark-border text-center">
            <Inbox className="w-8 h-8 text-gray-300 dark:text-dark-border mx-auto mb-2" />
            <p className="text-sm text-gray-500 italic">
              {t('access.no_requests', 'No care-team access requests or grants.')}
            </p>
          </div>
        )}

        {!loading && pending.length > 0 && (
          <div>
            <h4 className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-3">
              {t('access.pending_title', 'Pending — waiting for your answer')}
            </h4>
            <div className="space-y-3">
              {pending.map((grant) => (
                <div
                  key={grant.id}
                  data-testid="access-pending-row"
                  className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 p-4 bg-amber-50/60 dark:bg-amber-900/10 border border-amber-100 dark:border-amber-900/30 rounded-2xl"
                >
                  <div className="min-w-0">
                    <p className="font-bold text-sm text-brand-navy dark:text-dark-text truncate">
                      {grant.patient_name || grant.patient_id}
                    </p>
                    <p className="text-[11px] text-gray-500 dark:text-dark-muted">
                      {t('access.pending_row_sub', {
                        defaultValue: '{{permission}} access — shared with you',
                        permission: t(`access.permission_${grant.permission}`, grant.permission),
                      })}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      onClick={() => accept(grant)}
                      disabled={acting}
                      className="flex items-center space-x-1.5 px-3 py-2 bg-emerald-600 text-white rounded-xl text-xs font-bold hover:bg-emerald-700 disabled:opacity-50 transition-colors"
                    >
                      <Check className="w-3.5 h-3.5" />
                      <span>{t('access.accept', 'Accept')}</span>
                    </button>
                    <button
                      onClick={() => decline(grant)}
                      disabled={acting}
                      className="flex items-center space-x-1.5 px-3 py-2 border border-gray-200 dark:border-dark-border text-gray-600 dark:text-dark-muted rounded-xl text-xs font-bold hover:bg-gray-50 dark:hover:bg-dark-bg disabled:opacity-50 transition-colors"
                    >
                      <X className="w-3.5 h-3.5" />
                      <span>{t('access.decline', 'Decline')}</span>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {!loading && active.length > 0 && (
          <div>
            <h4 className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-3">
              {t('access.active_title', 'Records shared with you')}
            </h4>
            <div className="space-y-2">
              {active.map((grant) => (
                <Link
                  key={grant.id}
                  to={`/patients/${grant.patient_id}`}
                  className="flex items-center justify-between p-4 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-2xl hover:border-blue-400 transition-colors group"
                >
                  <div className="min-w-0">
                    <p className="font-bold text-sm text-brand-navy dark:text-dark-text truncate">
                      {grant.patient_name || grant.patient_id}
                    </p>
                    <p className="text-[11px] text-gray-400">
                      {t(`access.permission_${grant.permission}`, grant.permission)}
                      {grant.expires_at
                        ? ` · ${t('access.expires_date', {
                            defaultValue: 'Expires {{date}}',
                            date: new Date(grant.expires_at).toLocaleDateString(undefined, { dateStyle: 'medium' }),
                          })}`
                        : ''}
                    </p>
                  </div>
                  <span className="text-[10px] font-black uppercase tracking-widest text-blue-500 group-hover:text-blue-600">
                    {t('access.open_record', 'Open')}
                  </span>
                </Link>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
