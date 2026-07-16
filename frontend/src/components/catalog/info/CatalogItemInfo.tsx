/**
 * CatalogItemInfo — the Info-tab body for the selected catalog item.
 *
 * Replaces the flat `Object.entries(item)` dump with a registry-driven,
 * type-aware layout: fields are grouped into titled {@link InfoSection}s per
 * the {@link TYPE_FIELDS} registry, each rendered via the reusable
 * `components/ui` primitives (`KeyValueGrid` for label/value pairs,
 * `FormattedText` for rich-text prose). Keys not in the registry land in a
 * trailing "Additional fields" section, so no data is hidden.
 *
 * Phase 2: body uses `kv` + `richtext` kinds only (output is ~identical to the
 * pre-refactor flat list, now grouped under section headers). Phase 3 swaps
 * `kv` entries for specialized renderers (CodeBadge / ChipList / …) and adds
 * the remaining union kinds.
 *
 * Two render modes (unchanged API):
 *  - `hideHeader` (default in the workspace): body only — the preview pane
 *    already renders the authoritative name + actions.
 *  - full mode: a compact card with name + scope badge + actions (legacy path,
 *    retained for compatibility).
 */
import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { formatDistanceToNow } from 'date-fns';
import { el, enUS } from 'date-fns/locale';
import type { Locale } from 'date-fns';
import { Edit3, GitBranch, History as HistoryIcon, ExternalLink, AlertTriangle, Database, Activity, Pill, ShieldAlert, PersonStanding, Syringe, Network, Search, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { ScopeBadge } from '../ScopeBadge';
import { FormattedText } from '../../ui/FormattedText';
import { InfoSection } from '../../ui/InfoSection';
import { KeyValueGrid } from '../../ui/KeyValueGrid';
import type { KeyValueEntry } from '../../ui/KeyValueGrid';
import { CodeBadge } from '../../ui/CodeBadge';
import { ChipList } from '../../ui/ChipList';
import { BooleanPill } from '../../ui/BooleanPill';
import { CopyButton } from '../../ui/CopyButton';
import { EnumBadgeField } from './fields/EnumBadgeField';
import { RefRangesField } from './fields/RefRangesField';
import { DoseScheduleField } from './fields/DoseScheduleField';
import { ColorSwatchField } from './fields/ColorSwatchField';
import { IconPreviewField } from './fields/IconPreviewField';
import { getFieldCompleteness } from './completeness';
import type { CatalogItem, CatalogType } from '../../../types/catalog';
import { useAuthStore } from '../../../store/slices/authSlice';
import { useFieldDescriptors } from './useFieldDescriptors';
import { SECTION_META, type FieldDescriptor } from './fieldRegistry';

/** Format a raw value for the KeyValueGrid, mirroring the pre-refactor
 *  `renderValue`: arrays → comma-joined, objects → JSON, null/empty → null
 *  (KeyValueGrid renders the muted dash). */
function formatRawValue(value: unknown): React.ReactNode {
  if (value === null || value === undefined) return null;
  if (Array.isArray(value)) {
    return value.length ? value.map((v) => String(v)).join(', ') : null;
  }
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

/** Mechanical snake_case → Title Case label for additional/unknown fields. */
function labelFor(key: string): string {
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Stringify a raw value for case-insensitive filter matching. */
function stringifyValue(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (Array.isArray(value)) return value.map(stringifyValue).join(' ');
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value);
    } catch {
      return '';
    }
  }
  return String(value);
}

interface CatalogItemInfoProps {
  item: CatalogItem | null;
  total: number;
  /** The catalog type — drives which registry applies. When omitted, every
   *  field falls into "Additional fields" (graceful fallback). */
  catalogType?: CatalogType;
  domainRoute?: string | null;
  onEdit?: (item: CatalogItem) => void;
  onShowRelations?: (itemId: string) => void;
  onShowHistory?: (item: CatalogItem) => void;
  /** Jump to the Relations tab (drives the related-summary chip). */
  onJumpRelations?: () => void;
  /** Body-only mode (the preview pane renders the authoritative header). */
  hideHeader?: boolean;
}

/** Resolve a descriptor's label via i18n with a fallback. */
function useDescriptorLabel() {
  const { t } = useTranslation();
  return (d: FieldDescriptor) => t(d.labelKey, d.labelFallback);
}

/** Catalog-type → leading icon (matches the backend registrations' ui.icon). */
const TYPE_ICON: Record<CatalogType, LucideIcon> = {
  biomarker: Activity,
  medication: Pill,
  allergy: ShieldAlert,
  anatomy: PersonStanding,
  vaccine: Syringe,
  concept: Network,
};

export const CatalogItemInfo: React.FC<CatalogItemInfoProps> = ({
  item,
  total,
  catalogType,
  domainRoute,
  onEdit,
  onShowRelations,
  onShowHistory,
  onJumpRelations,
  hideHeader = false,
}) => {
  const { t, i18n } = useTranslation();
  const currentUserId = useAuthStore((s) => s.user?.id ?? null);
  const locale: Locale = i18n.language.startsWith('el') ? el : enUS;
  const labelForDescriptor = useDescriptorLabel();
  const { sections, unknowns } = useFieldDescriptors(catalogType, item);

  // Tier B: in-preview field filter. These hooks MUST run before the early
  // `if (!item)` return below — React requires a stable hook order across
  // renders. When item is null, sections/unknowns are [] so the memos are no-ops.
  const [query, setQuery] = useState('');
  const q = query.trim().toLowerCase();
  const filteredSections = useMemo(() => {
    if (!q || !item) return sections;
    const matches = (d: FieldDescriptor) =>
      labelForDescriptor(d).toLowerCase().includes(q) ||
      stringifyValue((item as Record<string, unknown>)[d.key]).toLowerCase().includes(q);
    return sections
      .map((s) => ({ ...s, descriptors: s.descriptors.filter(matches) }))
      .filter((s) => s.descriptors.length > 0);
  }, [sections, q, item, labelForDescriptor]);
  const filteredUnknowns = useMemo(() => {
    if (!q) return unknowns;
    return unknowns.filter(
      (u) =>
        labelFor(u.key).toLowerCase().includes(q) ||
        stringifyValue(u.value).toLowerCase().includes(q),
    );
  }, [unknowns, q]);

  if (!item) {
    const TypeIcon = catalogType ? TYPE_ICON[catalogType] : Database;
    return (
      <div className="rounded-xl border border-dashed border-gray-300 dark:border-gray-600 p-8 text-center">
        <TypeIcon className="w-8 h-8 mx-auto text-gray-300 dark:text-gray-600 mb-2" aria-hidden />
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

  const updated = item.updated_at
    ? formatDistanceToNow(new Date(item.updated_at), { addSuffix: true, locale })
    : null;

  // Tier A: related-summary + data-quality signals.
  const relationCount =
    typeof item.relation_count === 'number' ? item.relation_count : 0;
  const completeness = getFieldCompleteness(catalogType, item);

  /** Render one descriptor's value, switching on its `kind`. Unhandled kinds
   *  degrade to the formatted-raw kv value. */
  const renderFieldValue = (d: FieldDescriptor, src: CatalogItem): React.ReactNode => {
    const raw = (src as Record<string, unknown>)[d.key];
    switch (d.kind) {
      case 'richtext':
        return <FormattedText value={raw as string} />;
      case 'code':
        return (
          <CodeBadge
            code={raw as string}
            system={
              d.systemKey ? ((src as Record<string, unknown>)[d.systemKey] as string) : undefined
            }
          />
        );
      case 'chips':
        return <ChipList items={raw as string[]} variant={d.variant} />;
      case 'boolean':
        return (
          <BooleanPill value={raw as boolean} labelOn={d.labelOn} labelOff={d.labelOff} />
        );
      case 'enum':
        return <EnumBadgeField value={raw as string} options={d.options} tones={d.tones} />;
      case 'refranges':
        return <RefRangesField value={raw} />;
      case 'dose':
        return <DoseScheduleField value={raw} />;
      case 'color':
        return <ColorSwatchField value={raw} />;
      case 'icon':
        return (
          <IconPreviewField
            value={raw}
            color={
              d.colorKey ? ((src as Record<string, unknown>)[d.colorKey] as string) : undefined
            }
          />
        );
      default: {
        const text = formatRawValue(raw);
        if (text === null) return <span className="text-gray-400">—</span>;
        return (
          <span className="inline-flex items-center gap-1.5">
            <span className={d.mono ? 'font-mono break-all' : 'break-words'}>{text}</span>
            {d.copyable && raw !== null && raw !== undefined && (
              <CopyButton value={String(raw)} size={12} />
            )}
          </span>
        );
      }
    }
  };

  const body = (
    <div className="space-y-3">
      {/* Tier B: in-preview field filter. */}
      <div className="relative">
        <Search className="w-3.5 h-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" aria-hidden />
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t('catalogs.filter_fields', 'Filter fields…')}
          className="w-full pl-7 pr-7 py-1.5 text-xs rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40 text-gray-700 dark:text-gray-200 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
          aria-label={t('catalogs.filter_fields', 'Filter fields…')}
        />
        {query && (
          <button
            type="button"
            onClick={() => setQuery('')}
            className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 rounded"
            aria-label={t('common.clear', 'Clear')}
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
      {/* Tier A: summary bar — related count + data-quality badge. */}
      {(relationCount > 0 || !completeness.complete) && (
        <div className="flex flex-wrap items-center gap-2">
          {relationCount > 0 && onJumpRelations && (
            <button
              type="button"
              onClick={onJumpRelations}
              className="inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-xs font-medium bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 hover:bg-blue-100 dark:hover:bg-blue-900/50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
              title={t('catalogs.view_relations', 'View relations')}
            >
              <GitBranch className="w-3.5 h-3.5" aria-hidden />
              {t('catalogs.related_summary', {
                defaultValue: '{{count}} {{plural}}',
                count: relationCount,
                plural: relationCount === 1 ? 'relation' : 'relations',
              })}
            </button>
          )}
          {!completeness.complete && (
            <span
              className="inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-xs font-medium bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300"
              title={t('catalogs.completeness_missing', {
                defaultValue: 'Missing: {{fields}}',
                fields: completeness.missing.join(', '),
              })}
            >
              <AlertTriangle className="w-3.5 h-3.5" aria-hidden />
              {t('catalogs.completeness_needs_attention', 'Needs attention')}
            </span>
          )}
        </div>
      )}
      {filteredSections.map((section) => {
        const meta = SECTION_META[section.id];
        return (
          <InfoSection
            key={section.id}
            title={t(meta.labelKey, meta.labelFallback)}
            icon={meta.icon}
          >
            <dl className="divide-y divide-gray-100 dark:divide-gray-700">
              {section.descriptors.map((d) => (
                <div key={d.key} className="py-1.5 grid grid-cols-3 gap-3 text-sm">
                  <dt className="text-gray-500 dark:text-gray-400 col-span-1">
                    {labelForDescriptor(d)}
                  </dt>
                  <dd className="text-gray-800 dark:text-gray-100 col-span-2 min-w-0">
                    {renderFieldValue(d, item)}
                  </dd>
                </div>
              ))}
            </dl>
          </InfoSection>
        );
      })}
      {filteredUnknowns.length > 0 && (
        <InfoSection
          title={t(SECTION_META.additional.labelKey, SECTION_META.additional.labelFallback)}
          icon={SECTION_META.additional.icon}
          collapsible
          defaultOpen={false}
        >
          <KeyValueGrid
            entries={filteredUnknowns.map<KeyValueEntry>((u) => ({
              key: u.key,
              label: labelFor(u.key),
              value: formatRawValue(u.value),
            }))}
          />
        </InfoSection>
      )}
      <div className="flex items-center justify-between gap-2 pt-1">
        <p className="text-[11px] text-gray-400">
          {updated ? `${t('catalogs.updated_ago', 'Updated')} ${updated}` : ''}
        </p>
        <CopyButton
          value={JSON.stringify(item, null, 2)}
          label={t('catalogs.copy_json', 'Copy JSON')}
          size={12}
        />
      </div>
    </div>
  );

  // Body-only mode (the workspace renders the authoritative header + actions).
  if (hideHeader) {
    return body;
  }

  // Legacy full-card mode (retained for compatibility).
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
      <div className="p-5">{body}</div>
    </div>
  );
};

export default CatalogItemInfo;
