/**
 * InstancePreview — the default right-hand preview pane for the unified
 * instance browser. Renders a uniform {@link InstanceRow} as a scannable
 * detail card: a header (icon + type chip + label + date + status/badges)
 * and a rich-text body (the row's `description`, rendered via
 * {@link FormattedText} so HTML / Markdown / plain text all display correctly).
 *
 * This is the generic fallback. An adapter may supply its own richer preview
 * via `InstanceAdapter.renderPreview` (e.g. delegating to a dedicated entity
 * preview component); where that's absent, this component is used.
 *
 * Mirrors the master-detail "preview" half the list pages use
 * (`ExaminationPreview` etc.), but entity-agnostic so it works for all seven
 * instance types out of the box.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { formatDistanceToNow, isValid } from 'date-fns';
import { el, enUS } from 'date-fns/locale';
import type { Locale } from 'date-fns';
import { Inbox, ExternalLink, Calendar } from 'lucide-react';
import { DynamicIcon } from '../ui/DynamicIcon';
import { FormattedText } from '../ui/FormattedText';
import type { InstanceRow } from './types';

export interface InstancePreviewProps {
  row: InstanceRow | null;
  /** Optional "Open in domain" link (the adapter's detailRoute). */
  detailRoute?: string | null;
  /** Empty-state hint shown when no row is selected. */
  emptyHint?: string;
}

export const InstancePreview: React.FC<InstancePreviewProps> = ({
  row,
  detailRoute,
  emptyHint,
}) => {
  const { t, i18n } = useTranslation();
  const locale: Locale = i18n.language.startsWith('el') ? el : enUS;

  if (!row) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-6">
        <Inbox className="w-8 h-8 text-gray-300 dark:text-gray-600 mb-2" />
        <p className="text-sm font-medium text-gray-500 dark:text-dark-muted">
          {emptyHint ?? t('instances.preview_empty', 'Select a record to preview')}
        </p>
      </div>
    );
  }

  const dateObj = row.date ? new Date(row.date) : null;
  const relDate =
    dateObj && isValid(dateObj)
      ? formatDistanceToNow(dateObj, { addSuffix: true, locale })
      : null;

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header */}
      <div className="shrink-0 px-6 pt-6 pb-4 border-b border-gray-100 dark:border-dark-border">
        <div className="flex items-start gap-3">
          <div className="p-2.5 bg-blue-50 dark:bg-blue-900/20 rounded-xl text-blue-600 dark:text-blue-400 shrink-0">
            {row.icon ? (
              <DynamicIcon icon={row.icon} className="w-5 h-5" />
            ) : (
              <Inbox className="w-5 h-5" />
            )}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[10px] font-bold uppercase tracking-widest text-blue-500">
                {t(`instances.type_${row.type}`, row.type)}
              </span>
              {row.status && (
                <span
                  className="text-[10px] font-bold uppercase tracking-wide rounded px-1.5 py-0.5"
                  style={
                    row.statusColor
                      ? { backgroundColor: `${row.statusColor}1a`, color: row.statusColor }
                      : undefined
                  }
                >
                  {row.status}
                </span>
              )}
            </div>
            <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text break-words">
              {row.label}
            </h3>
            {(relDate || row.subtitle) && (
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1 text-xs text-gray-400">
                {relDate && (
                  <span className="inline-flex items-center gap-1">
                    <Calendar className="w-3 h-3" /> {relDate}
                  </span>
                )}
                {row.subtitle && <span className="truncate">{row.subtitle}</span>}
              </div>
            )}
          </div>
          {detailRoute && (
            <a
              href={detailRoute}
              className="p-1.5 text-gray-400 hover:text-blue-500 shrink-0"
              title={t('instances.open_in_domain', 'Open in domain view')}
            >
              <ExternalLink className="w-4 h-4" />
            </a>
          )}
        </div>
        {row.badges && row.badges.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-3">
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
                {b.label}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Rich-text body */}
      <div className="flex-1 overflow-y-auto px-6 py-5 custom-scrollbar">
        <FormattedText value={row.description} />
      </div>
    </div>
  );
};
