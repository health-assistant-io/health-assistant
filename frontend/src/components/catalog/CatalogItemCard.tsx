/**
 * CatalogItemCard — the catalog counterpart of {@link InstanceCard}.
 *
 * Given a {@link CatalogSelection} (`{type, id}`), it fetches the catalog item
 * via {@link getCatalogItem}, projects it through the metadata-driven
 * {@link TYPE_FIELDS} registry (the single source of truth for each catalog
 * type's field layout), and renders a compact info card: type icon + type chip
 * + name + scope badge, followed by the type's key fields — richtext collapses
 * to a one-line snippet, chips render as semantic pills, everything else
 * stringifies. This is the inline-form companion shown under a
 * {@link CatalogItemPicker} in `displayMode="cards"`, so a selected catalog
 * definition (e.g. a medication picked while recording a prescription) is
 * visible at a glance — indications, dosage, side effects, contraindications —
 * instead of a bare chip.
 *
 * Resolved items are cached by `type:id` for the session so cards don't refetch
 * on every parent re-render (form state changes, etc.). UUID keys are globally
 * unique, so the cache is safe across patients/tenants.
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ExternalLink, X, AlertCircle } from 'lucide-react';
import { ScopeBadge } from './ScopeBadge';
import { ChipList } from '../ui/ChipList';
import { Modal } from '../ui/Modal';
import { CatalogItemInfo } from './info/CatalogItemInfo';
import { getCatalogTypeIcon } from './catalogTypeIcons';
import { toSnippet } from '../../utils/textFormat';
import { getCatalogItem } from '../../services/catalogService';
import { useAuthStore } from '../../store/slices/authSlice';
import { useFieldDescriptors } from './info/useFieldDescriptors';
import {
  SECTION_ORDER,
  type FieldDescriptor,
  type SectionId,
} from './info/fieldRegistry';
import type { CatalogItem, CatalogSelection, CatalogType } from '../../types/catalog';

/**
 * Sections rendered in the compact body. `identity` is skipped (the name lives
 * in the header; the taxonomy class becomes the subtitle); `coding`,
 * `presentation`, `display`, `reference_ranges`, `meta`, `additional` are
 * skipped to keep the card compact and clinically focused — the full detail
 * remains one click away in the Catalog workspace.
 */
const BODY_SECTIONS: ReadonlySet<SectionId> = new Set<SectionId>([
  'clinical',
  'safety',
  'reactions',
  'unit',
  'targets',
  'schedule',
  'aliases',
]);

export interface CatalogItemCardProps {
  /** Which catalog item to render (fetched via `/catalogs/{type}/{id}`). */
  selection: CatalogSelection;
  /** Remove affordance — rendered as an X in the card's action area. */
  onRemove?: () => void;
  /** Extra trailing content (e.g. a relation-type select). */
  actions?: React.ReactNode;
  /**
   * Optional content rendered full-width below the main row (inside the card
   * border, with a subtle divider). Mirrors {@link InstanceCard.footer}.
   */
  footer?: React.ReactNode;
  /**
   * Soft cap on the number of info fields rendered in the body, so a densely
   * populated item can't make the card unbounded. Defaults to 6.
   */
  maxFields?: number;
  /**
   * Show the "open in catalog" affordance (defaults to true). Opens an in-app
   * overlay with the full {@link CatalogItemInfo} rather than navigating — the
   * caller form/modal is never left, and the standalone PWA never exits to a
   * browser tab.
   */
  showOpenLink?: boolean;
  className?: string;
}

interface Resolved {
  item: CatalogItem;
}

const cache = new Map<string, Resolved>();
const cacheKey = (type: string, id: string) => `${type}:${id}`;

/** Force a re-fetch on next render (e.g. after the item is mutated). */
export function invalidateCatalogItemCard(type: string, id: string): void {
  cache.delete(cacheKey(type, id));
}

/** Compact value renderer for one field descriptor. Richtext → snippet,
 *  chips → {@link ChipList}, everything else → a defensive string. */
function renderFieldValue(
  descriptor: FieldDescriptor,
  raw: unknown,
): { node: React.ReactNode; isEmpty: boolean } {
  if (raw === null || raw === undefined) return { node: null, isEmpty: true };
  if (descriptor.kind === 'chips') {
    const items = Array.isArray(raw) ? (raw as Array<string | null | undefined>) : [];
    return { node: <ChipList items={items} variant={descriptor.variant} />, isEmpty: items.length === 0 };
  }
  if (descriptor.kind === 'richtext') {
    const snippet = toSnippet(typeof raw === 'string' ? raw : String(raw), 160);
    return { node: snippet || null, isEmpty: !snippet };
  }
  if (typeof raw === 'boolean') {
    if (descriptor.kind === 'boolean') {
      return { node: raw ? descriptor.labelOn : descriptor.labelOff, isEmpty: false };
    }
    return { node: raw ? 'Yes' : 'No', isEmpty: false };
  }
  if (Array.isArray(raw)) {
    const text = raw.map((v) => String(v)).join(', ');
    return { node: text, isEmpty: text === '' };
  }
  if (typeof raw === 'object') {
    let text = '';
    try {
      text = JSON.stringify(raw);
    } catch {
      text = '';
    }
    return { node: text, isEmpty: text === '' };
  }
  const text = String(raw);
  return { node: text, isEmpty: text.trim() === '' };
}

export const CatalogItemCard: React.FC<CatalogItemCardProps> = ({
  selection,
  onRemove,
  actions,
  footer,
  maxFields = 6,
  showOpenLink = true,
  className = '',
}) => {
  const { t } = useTranslation();
  const currentUserId = useAuthStore((s) => s.user?.id ?? null);
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
    getCatalogItem(selection.type, selection.id)
      .then((data) => {
        const next: Resolved = { item: data as CatalogItem };
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
  }, [key, selection.type, selection.id]);

  // Hook must run unconditionally; it returns empty sections while loading.
  const { sections } = useFieldDescriptors(
    selection.type as CatalogType,
    resolved?.item ?? null,
  );

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
            {t('catalogs.card_unavailable', 'Catalog entry unavailable')}
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

  const item = resolved.item;
  const TypeIcon = getCatalogTypeIcon(selection.type);
  const name = (item.name ?? selection.label ?? selection.id) as string;
  const classConcept = item.class_concept_name
    ? String(item.class_concept_name)
    : null;

  // Project the body fields: keep BODY_SECTIONS, in SECTION_ORDER, capped.
  const bodyFields: { descriptor: FieldDescriptor; node: React.ReactNode }[] = [];
  outer: for (const section of SECTION_ORDER) {
    if (!BODY_SECTIONS.has(section)) continue;
    const group = sections.find((s) => s.id === section);
    if (!group) continue;
    for (const descriptor of group.descriptors) {
      if (bodyFields.length >= maxFields) break outer;
      const raw = (item as Record<string, unknown>)[descriptor.key];
      const { node, isEmpty } = renderFieldValue(descriptor, raw);
      if (isEmpty) continue;
      bodyFields.push({ descriptor, node });
    }
  }

  return (
    <div
      className={`rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 transition-colors hover:border-blue-300 dark:hover:border-blue-700 ${className}`}
    >
      <div className="flex items-start gap-3 px-4 py-3">
        {/* Icon */}
        <div className="shrink-0 mt-0.5 p-2 bg-blue-50 dark:bg-blue-900/20 rounded-lg text-blue-600 dark:text-blue-400">
          <TypeIcon className="w-4 h-4" />
        </div>

        {/* Body */}
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[10px] font-bold uppercase tracking-widest text-blue-500 capitalize">
              {selection.type}
            </span>
            <ScopeBadge
              scope={item.scope}
              created_by={item.created_by}
              currentUserId={currentUserId}
            />
          </div>
          <p className="font-semibold text-sm text-gray-900 dark:text-dark-text truncate mt-0.5">
            {name}
          </p>
          {classConcept && (
            <p className="text-xs text-gray-500 dark:text-gray-400 truncate mt-0.5">
              {classConcept}
            </p>
          )}

          {bodyFields.length > 0 && (
            <dl className="mt-2 space-y-1.5">
              {bodyFields.map(({ descriptor, node }) => (
                <div key={descriptor.key} className="flex flex-col gap-0.5">
                  <dt className="text-[10px] font-bold uppercase tracking-wider text-gray-400 dark:text-gray-500">
                    {t(descriptor.labelKey, descriptor.labelFallback)}
                  </dt>
                  <dd className="text-xs text-gray-700 dark:text-gray-200 break-words">
                    {node}
                  </dd>
                </div>
              ))}
            </dl>
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
              title={t('catalogs.open_in_domain', 'Open in catalog')}
              aria-label={t('catalogs.open_in_domain', 'Open in catalog')}
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
          modal (Escape closes only this overlay). */}
      {detailOpen && (
        <Modal
          isOpen
          onClose={() => setDetailOpen(false)}
          title={name}
          size="lg"
          headerIcon={
            <div className="p-2 bg-blue-50 dark:bg-blue-900/20 rounded-lg text-blue-600 dark:text-blue-400">
              <TypeIcon className="w-4 h-4" />
            </div>
          }
        >
          <CatalogItemInfo
            item={item}
            catalogType={selection.type as CatalogType}
            total={0}
            hideHeader
          />
        </Modal>
      )}
    </div>
  );
};

export default CatalogItemCard;
