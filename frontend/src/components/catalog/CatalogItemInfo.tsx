/**
 * Catalog item Info tab — the inline detail of the selected item (Layer 2).
 * Replaces the old click-to-preview drawer (`CatalogItemDrawer`): the same
 * field list + actions (Edit / Relations / History / Open-in-domain), but
 * rendered in-place under the tabs instead of a slide-over.
 *
 * When no item is selected ("All"), renders a lightweight catalog overview.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { formatDistanceToNow } from 'date-fns';
import { el, enUS } from 'date-fns/locale';
import type { Locale } from 'date-fns';
import { Edit3, GitBranch, History as HistoryIcon, ExternalLink } from 'lucide-react';
import { ScopeBadge } from './ScopeBadge';
import type { CatalogItem } from '../../types/catalog';
import { useAuthStore } from '../../store/slices/authSlice';

const META_KEYS = new Set([
  'id', 'tenant_id', 'created_by', 'updated_by', 'created_at', 'updated_at',
  'scope', 'is_current', 'version', 'is_custom',
]);

function renderValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (Array.isArray(value)) return value.length ? value.map(String).join(', ') : '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

const labelFor = (key: string): string =>
  key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

interface CatalogItemInfoProps {
  item: CatalogItem | null;
  total: number;
  domainRoute?: string | null;
  onEdit?: (item: CatalogItem) => void;
  onShowRelations?: (itemId: string) => void;
  onShowHistory?: (item: CatalogItem) => void;
  /**
   * Render only the field list, without the card/name/actions header. Used when
   * the parent (the preview pane) already renders an authoritative header.
   */
  hideHeader?: boolean;
}

export const CatalogItemInfo: React.FC<CatalogItemInfoProps> = ({
  item,
  total,
  domainRoute,
  onEdit,
  onShowRelations,
  onShowHistory,
  hideHeader = false,
}) => {
  const { t, i18n } = useTranslation();
  const currentUserId = useAuthStore((s) => s.user?.id ?? null);
  const locale: Locale = i18n.language.startsWith('el') ? el : enUS;

  if (!item) {
    return (
      <div className="rounded-xl border border-dashed border-gray-300 dark:border-gray-600 p-8 text-center">
        <p className="text-sm text-gray-500 dark:text-gray-400">
          {t('catalogs.overview_empty', {
            defaultValue: 'No item selected. {{total}} items in this catalog.',
            total,
          })}
        </p>
        <p className="text-xs text-gray-400 mt-1">
          {t('catalogs.overview_hint', 'Pick an item above to see its details.')}
        </p>
      </div>
    );
  }

  const entries = Object.entries(item).filter(
    ([k, v]) => !META_KEYS.has(k) && v !== null && v !== undefined,
  );
  const updated = item.updated_at
    ? formatDistanceToNow(new Date(item.updated_at), { addSuffix: true, locale })
    : null;

  // Headerless mode: just the field list (parent renders the name/actions).
  if (hideHeader) {
    return (
      <div className="space-y-1">
        {entries.length === 0 ? (
          <p className="text-sm text-gray-400 py-4 text-center">
            {t('catalogs.no_fields', 'No fields to display.')}
          </p>
        ) : (
          <dl className="divide-y divide-gray-100 dark:divide-gray-700">
            {entries.map(([key, value]) => (
              <div key={key} className="py-2 grid grid-cols-3 gap-3 text-sm">
                <dt className="text-gray-500 dark:text-gray-400 col-span-1">
                  {labelFor(key)}
                </dt>
                <dd className="text-gray-800 dark:text-gray-100 col-span-2 break-words">
                  {renderValue(value)}
                </dd>
              </div>
            ))}
          </dl>
        )}
        {updated && (
          <p className="text-[11px] text-gray-400 pt-2">
            {t('catalogs.updated_ago', 'Updated')} {updated}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 flex flex-col">
      <div className="flex items-start justify-between gap-2 p-5 border-b border-gray-100 dark:border-gray-700">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-lg font-semibold truncate">
              {String(item.name ?? item.slug ?? 'Item')}
            </h3>
            <ScopeBadge
              scope={item.scope}
              created_by={item.created_by}
              currentUserId={currentUserId}
            />
          </div>
          {item.slug && item.slug !== item.name && (
            <p className="text-xs text-gray-400 font-mono mt-0.5">{String(item.slug)}</p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {onEdit && (
            <button
              onClick={() => onEdit(item)}
              className="flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700"
            >
              <Edit3 className="w-4 h-4" /> {t('catalogs.edit', 'Edit')}
            </button>
          )}
          {onShowRelations && (
            <button
              onClick={() => onShowRelations(String(item.id))}
              className="flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-lg border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700"
            >
              <GitBranch className="w-4 h-4" /> {t('catalogs.tab_relations', 'Relations')}
            </button>
          )}
          {onShowHistory && (
            <button
              onClick={() => onShowHistory(item)}
              className="flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-lg border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700"
            >
              <HistoryIcon className="w-4 h-4" /> {t('catalogs.audit_history_title', 'History')}
            </button>
          )}
          {domainRoute && (
            <a
              href={domainRoute}
              className="flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-lg border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700"
            >
              <ExternalLink className="w-4 h-4" /> {t('catalogs.open_in_domain', 'Open')}
            </a>
          )}
        </div>
      </div>

      <div className="p-5 space-y-1">
        <dl className="divide-y divide-gray-100 dark:divide-gray-700">
          {entries.map(([key, value]) => (
            <div key={key} className="py-2 grid grid-cols-3 gap-3 text-sm">
              <dt className="text-gray-500 dark:text-gray-400 col-span-1">
                {labelFor(key)}
              </dt>
              <dd className="text-gray-800 dark:text-gray-100 col-span-2 break-words">
                {renderValue(value)}
              </dd>
            </div>
          ))}
        </dl>
        {updated && (
          <p className="text-[11px] text-gray-400 pt-2">
            {t('catalogs.updated_ago', 'Updated')} {updated}
          </p>
        )}
      </div>
    </div>
  );
};
