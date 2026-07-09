/**
 * Audit history modal — shows the append-only trail for one catalog item
 * (Phase B). Each entry records the operation, who performed it, when, and any
 * scope transition. Newest-first (the backend sorts). Uses the shared
 * `<Modal>` shell + `date-fns` relative-time (mirrors NotificationItem).
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { formatDistanceToNow } from 'date-fns';
import { el, enUS } from 'date-fns/locale';
import type { Locale } from 'date-fns';
import { History } from 'lucide-react';
import { Modal } from '../ui/Modal';
import { LoadingState } from '../ui/LoadingState';
import { getCatalogItemHistory } from '../../services/catalogService';
import type { CatalogAuditEntry, CatalogType } from '../../types/catalog';

interface CatalogAuditHistoryModalProps {
  type: CatalogType | string;
  itemId: string | null;
  itemName?: string;
  onClose: () => void;
}

const OPERATION_CLASSES: Record<string, string> = {
  create: 'text-emerald-600 dark:text-emerald-400',
  update: 'text-blue-600 dark:text-blue-400',
  delete: 'text-red-600 dark:text-red-400',
  promote: 'text-violet-600 dark:text-violet-400',
  demote: 'text-amber-600 dark:text-amber-400',
};

function pickLocale(lng: string): Locale {
  return lng.startsWith('el') ? el : enUS;
}

export const CatalogAuditHistoryModal: React.FC<
  CatalogAuditHistoryModalProps
> = ({ type, itemId, itemName, onClose }) => {
  const { t, i18n } = useTranslation();
  const [entries, setEntries] = useState<CatalogAuditEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isOpen = itemId !== null;

  useEffect(() => {
    if (!isOpen || !itemId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    getCatalogItemHistory(type, itemId)
      .then((resp) => {
        if (!cancelled) setEntries(resp.items);
      })
      .catch(() => {
        if (!cancelled) setError(t('catalogs.audit_load_error'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isOpen, itemId, type, t]);

  const locale = pickLocale(i18n.language);

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={t('catalogs.audit_history_title')}
      className="max-w-xl"
    >
      {itemName && (
        <p className="text-sm text-gray-500 dark:text-gray-400 -mt-2 mb-4">
          {itemName}
        </p>
      )}
      {loading ? (
        <LoadingState variant="section" message="Loading…" />
      ) : error ? (
        <p className="text-sm text-red-500">{error}</p>
      ) : entries.length === 0 ? (
        <p className="text-sm text-gray-500 dark:text-gray-400 py-6 text-center">
          {t('catalogs.no_audit_entries')}
        </p>
      ) : (
        <ol className="space-y-3">
          {entries.map((entry) => (
            <li
              key={entry.id}
              className="flex items-start gap-3 rounded-lg border border-gray-100 dark:border-gray-700 px-3 py-2"
            >
              <History className="w-4 h-4 mt-0.5 text-gray-400 shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span
                    className={`text-xs font-bold uppercase tracking-wide ${
                      OPERATION_CLASSES[entry.operation] ?? 'text-gray-500'
                    }`}
                  >
                    {t(`catalogs.op_${entry.operation}`, entry.operation)}
                  </span>
                  {entry.from_scope && entry.to_scope && (
                    <span className="text-xs text-gray-500 dark:text-gray-400">
                      {entry.from_scope} → {entry.to_scope}
                    </span>
                  )}
                </div>
                <p className="text-sm text-gray-700 dark:text-gray-200 truncate">
                  {entry.item_name || t('catalogs.audit_deleted_item')}
                </p>
                <p className="text-xs text-gray-400 mt-0.5">
                  {entry.user_email || t('catalogs.audit_unknown_user')}
                  {' · '}
                  {entry.created_at
                    ? formatDistanceToNow(new Date(entry.created_at), {
                        addSuffix: true,
                        locale,
                      })
                    : ''}
                </p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Modal>
  );
};
