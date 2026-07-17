/**
 * InstanceCard — a compact basic-info card for one selected patient instance
 * (examination, medication, vaccine, observation, event, allergy, document).
 *
 * Given a {@link InstanceSelection} (`{type, id}`), it resolves the adapter,
 * fetches the record via `adapter.fetchOne(id, patientId)`, projects it with
 * `adapter.toRow`, and renders the uniform {@link InstanceRow} as a small card:
 * icon + type chip + label + relative date + status badge + extra badges +
 * subtitle, with an optional "open in domain" link and remove button.
 *
 * This is the inline-form counterpart of {@link InstancePreview}: InstancePreview
 * is the heavy browse-modal detail pane (header + rich-text body); InstanceCard
 * is the lightweight card shown under an {@link InstancePicker} in
 * `displayMode="cards"` so a linked record (e.g. an examination attached to a
 * medication) is visible at a glance instead of a bare chip.
 *
 * Resolved rows are cached by `type:id` for the session so cards don't refetch
 * on every parent re-render (form state changes, etc.). UUID keys are globally
 * unique, so the cache is safe across patients.
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { formatDistanceToNow, isValid } from 'date-fns';
import { el, enUS } from 'date-fns/locale';
import type { Locale } from 'date-fns';
import { Inbox, ExternalLink, Calendar, X, AlertCircle } from 'lucide-react';
import { DynamicIcon } from '../ui/DynamicIcon';
import { Modal } from '../ui/Modal';
import { InstancePreview } from './InstancePreview';
import { getInstanceDetail } from './detailViewRegistry';
import { toPlainText } from '../../utils/textFormat';
import { getAdapter } from './instanceRegistry';
import type { InstanceRow, InstanceSelection } from './types';

/**
 * Defensive coercion of a row display field to a render-safe string. Adapters
 * are contracted to store strings on {@link InstanceRow} (status/label/badges),
 * but some FHIR-derived fields slip through as objects ({@code {text}}) — this
 * prevents a white-screen crash by falling back to the object's `.text` then to
 * a plain string, never returning a non-string to React.
 */
function safeText(v: unknown): string {
  if (v == null) return '';
  if (typeof v === 'string') return v;
  if (typeof v === 'object') {
    const obj = v as { text?: string; coding?: Array<{ display?: string }> };
    return obj.text || obj.coding?.[0]?.display || '';
  }
  return String(v);
}

export interface InstanceCardProps {
  /** Which record to render (resolved via the adapter for `type`). */
  selection: InstanceSelection;
  /** Patient scope (required for patient-scoped adapters like allergies). */
  patientId?: string;
  /** Remove affordance — rendered as an X in the card's action area. */
  onRemove?: () => void;
  /** Extra trailing content (e.g. a relation-type select). */
  actions?: React.ReactNode;
  /**
   * Optional content rendered full-width below the main row (inside the card
   * border, with a subtle divider). Use for per-link form controls such as the
   * "reason for visit" / "notes" inputs on attach surfaces.
   */
  footer?: React.ReactNode;
  /** Show the "open in domain" affordance (opens an in-app overlay with the
   *  full {@link InstancePreview}, so the caller form/modal is never navigated
   *  away from — safe in the standalone PWA where a new tab would exit the
   *  app). Defaults to true. */
  showOpenLink?: boolean;
  className?: string;
}

interface Resolved {
  row: InstanceRow;
  route: string | null;
}

const cache = new Map<string, Resolved>();
const cacheKey = (type: string, id: string) => `${type}:${id}`;

/** Force a re-fetch on next render (e.g. after the record is mutated). */
export function invalidateInstanceCard(type: string, id: string): void {
  cache.delete(cacheKey(type, id));
}

export const InstanceCard: React.FC<InstanceCardProps> = ({
  selection,
  patientId,
  onRemove,
  actions,
  footer,
  showOpenLink = true,
  className = '',
}) => {
  const { t, i18n } = useTranslation();
  const locale: Locale = i18n.language.startsWith('el') ? el : enUS;
  const [detailOpen, setDetailOpen] = useState(false);
  const key = cacheKey(selection.type, selection.id);

  const [resolved, setResolved] = useState<Resolved | null>(cache.get(key) ?? null);
  const [loading, setLoading] = useState(!cache.has(key));
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const cached = cache.get(key);
    if (cached) {
      setResolved(cached);
      setLoading(false);
      setError(false);
      return;
    }
    setLoading(true);
    setError(false);
    getAdapter(selection.type)
      .fetchOne(selection.id, patientId)
      .then((item) => {
        const adapter = getAdapter(selection.type);
        const row = adapter.toRow(item);
        const route = adapter.detailRoute(item as never);
        const next: Resolved = { row, route };
        cache.set(key, next);
        if (!cancelled) {
          setResolved(next);
          setError(false);
        }
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [key, selection.type, selection.id, patientId]);

  const dateObj = resolved?.row.date ? new Date(resolved.row.date) : null;
  const relDate =
    dateObj && isValid(dateObj)
      ? formatDistanceToNow(dateObj, { addSuffix: true, locale })
      : null;

  // Loading skeleton.
  if (loading) {
    return (
      <div
        className={`flex items-center gap-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-4 py-3 ${className}`}
      >
        <div className="h-8 w-8 rounded-lg bg-gray-100 dark:bg-gray-800 animate-pulse shrink-0" />
        <div className="flex-1 space-y-2">
          <div className="h-3 w-1/3 bg-gray-100 dark:bg-gray-800 rounded animate-pulse" />
          <div className="h-3.5 w-2/3 bg-gray-100 dark:bg-gray-800 rounded animate-pulse" />
        </div>
      </div>
    );
  }

  // Error / not-found fallback: surface whatever the selection cached.
  if (error || !resolved) {
    const fallbackLabel = selection.label ?? selection.id;
    return (
      <div
        className={`flex items-center gap-3 rounded-xl border border-amber-200 dark:border-amber-500/30 bg-amber-50/60 dark:bg-amber-900/10 px-4 py-3 ${className}`}
      >
        <AlertCircle className="w-5 h-5 text-amber-500 shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-gray-700 dark:text-gray-200 truncate">
            {fallbackLabel}
          </p>
          <p className="text-[11px] text-amber-600 dark:text-amber-400">
            {t('instances.card_unavailable', 'Record unavailable')}
          </p>
        </div>
        {onRemove && (
          <button
            type="button"
            onClick={onRemove}
            className="p-1 rounded-full text-gray-400 hover:bg-red-100 hover:text-red-600 dark:hover:bg-red-900/30"
            title={t('common.remove', 'Remove')}
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
    );
  }

  const row = resolved.row;

  return (
    <div
      className={`rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 transition-colors hover:border-blue-300 dark:hover:border-blue-700 ${className}`}
    >
      <div className="flex items-start gap-3 px-4 py-3">
        {/* Icon */}
        <div className="shrink-0 mt-0.5 p-2 bg-blue-50 dark:bg-blue-900/20 rounded-lg text-blue-600 dark:text-blue-400">
          {row.icon ? (
            <DynamicIcon icon={row.icon} className="w-4 h-4" />
          ) : (
            <Inbox className="w-4 h-4" />
          )}
        </div>

        {/* Body */}
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[10px] font-bold uppercase tracking-widest text-blue-500">
              {t(`instances.type_${row.type}`, row.type)}
            </span>
          {row.status && (
            <span
              className="text-[10px] font-bold uppercase tracking-wide rounded px-1.5 py-0.5"
              style={
                row.statusColor
                  ? { backgroundColor: `${row.statusColor}1a`, color: row.statusColor }
                  : { backgroundColor: 'rgba(107,114,128,0.12)', color: '#6b7280' }
              }
            >
              {safeText(row.status)}
            </span>
          )}
            {relDate && (
              <span className="inline-flex items-center gap-0.5 text-[11px] text-gray-400">
                <Calendar className="w-3 h-3" /> {relDate}
              </span>
            )}
          </div>
        <p className="font-semibold text-sm text-gray-900 dark:text-dark-text truncate mt-0.5">
          {safeText(row.label)}
        </p>
        {row.subtitle && (
          <p className="text-xs text-gray-500 dark:text-gray-400 truncate mt-0.5">
            {toPlainText(safeText(row.subtitle))}
          </p>
        )}
          {row.badges && row.badges.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1.5">
              {row.badges.map((b, i) => (
              <span
                key={i}
                className="text-[10px] font-bold uppercase tracking-wide rounded px-1.5 py-0.5 bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                style={
                  b.color
                    ? { backgroundColor: `${b.color}1a`, color: b.color }
                    : undefined
                }
              >
                {safeText(b.label)}
              </span>
              ))}
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1 shrink-0">
          {actions}
          {showOpenLink && (
            <button
              type="button"
              onClick={() => setDetailOpen(true)}
              className="p-1.5 text-gray-400 hover:text-blue-500"
              title={t('instances.open_in_domain', 'Open in domain view')}
              aria-label={t('instances.open_in_domain', 'Open in domain view')}
            >
              <ExternalLink className="w-4 h-4" />
            </button>
          )}
          {onRemove && (
            <button
              type="button"
              onClick={onRemove}
              className="p-1 rounded-full text-gray-400 hover:bg-red-100 hover:text-red-600 dark:hover:bg-red-900/30"
              title={t('common.remove', 'Remove')}
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>

      {footer && (
        <div className="px-4 pb-3 pt-1 border-t border-gray-100 dark:border-gray-700 mt-1">
          {footer}
        </div>
      )}

      {/* Detail overlay — in-app so the form/modal behind it is never navigated
          away from (and the standalone PWA never exits to a browser tab). The
          shared Modal is stack-safe, so this layers correctly on a parent form
          modal (Escape closes only this overlay). The body reuses the entity's
          own rich detail component via the detail registry (e.g. examinations
          render the full ExaminationPreview — the single source of truth — not
          the thin generic InstancePreview); unregistered types fall back. */}
      {detailOpen && (
        <Modal
          isOpen
          onClose={() => setDetailOpen(false)}
          title={safeText(row.label)}
          size="lg"
          bodyClassName="p-0"
          headerIcon={
            <div className="p-2 bg-blue-50 dark:bg-blue-900/20 rounded-lg text-blue-600 dark:text-blue-400">
              {row.icon ? (
                <DynamicIcon icon={row.icon} className="w-4 h-4" />
              ) : (
                <Inbox className="w-4 h-4" />
              )}
            </div>
          }
        >
          {(() => {
            const Detail = getInstanceDetail(selection.type);
            if (Detail) {
              return <Detail id={selection.id} patientId={patientId} />;
            }
            return <InstancePreview row={row} />;
          })()}
        </Modal>
      )}
    </div>
  );
};
