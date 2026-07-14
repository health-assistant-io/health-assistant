/**
 * CatalogItemPicker — reusable search-and-select for catalog items.
 *
 * Two discovery paths feed one controlled selection:
 *   1. **Inline type-ahead** — debounced cross-catalog {@link searchCatalogs};
 *      results render as `<TYPE-chip> <label>`.
 *   2. **Browse button** (grid icon inside the input) — opens
 *      {@link CatalogPickerBrowseModal} with the full catalog-type dropdown
 *      (default *All*), search, scope, and per-row Add/Added toggles.
 *
 * Selection model:
 *   - `mode: 'single'` → picking replaces the selection (length ≤ 1).
 *   - `mode: 'multi'`  → appends; deduped by `type:id` (+ `relation` when used
 *     in relation mode).
 *
 * Relation binding: pass `relationPicker` to surface a relation-type dropdown
 * on each selected chip (output entries carry `relation`). Mirrors the
 * `CatalogRelationsEditor` pattern, which now delegates to this component.
 *
 * Controlled: `value: CatalogSelection[]` + `onChange`. The selected chips
 * render below the input with a remove (X) each.
 */
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Search, X, LayoutGrid } from 'lucide-react';
import { Portal } from '../ui/Portal';
import { RelationTypeSelect } from './RelationTypeSelect';
import { CatalogPickerBrowseModal } from './CatalogPickerBrowseModal';
import { searchCatalogs } from '../../services/catalogService';
import {
  DEFAULT_RELATION,
  RELATION_OPTION_GROUPS,
  type RelationOptionGroup,
} from './catalogRelationTypes';
import type {
  CatalogItem,
  CatalogSearchHit,
  CatalogSelection,
  CatalogType,
} from '../../types/catalog';

export interface CatalogItemPickerProps {
  /** Always an array; in `single` mode length is 0 or 1. */
  value: CatalogSelection[];
  onChange: (next: CatalogSelection[]) => void;
  mode?: 'single' | 'multi';
  /** Restrict to a subset of catalog types; default = all registered. */
  allowedTypes?: CatalogType[];
  /** ConceptKind value (e.g. `'event_category'`, `'specialty'`) that narrows
   *  the `concept` catalog. Only meaningful when `allowedTypes` includes
   *  `'concept'`; ignored for other catalogs. Lets the same picker power
   *  examination/event-category + specialty selection. */
  conceptKind?: string;
  /** Enable relation binding (chips get a relation dropdown). */
  relationPicker?: {
    /** Override the default option groups (mirrors ConceptRelationType). */
    options?: RelationOptionGroup[];
    /** Relation applied to new picks; default 'AFFECTS'. */
    defaultRelation?: string;
  };
  placeholder?: string;
  className?: string;
  disabled?: boolean;
  /** When true, a full-width block input is rendered instead of the compact
   *  inline one (useful inside form rows). */
  block?: boolean;
}

/** Dedup key — includes relation so the same item can be bound two ways.
 *  Exported so consumers (e.g. CatalogRelationsEditor) can diff consistently. */
export const selectionKey = (s: CatalogSelection): string =>
  `${s.type}:${s.id}${s.relation ? `:${s.relation}` : ''}`;

export const CatalogItemPicker: React.FC<CatalogItemPickerProps> = ({
  value,
  onChange,
  mode = 'multi',
  allowedTypes,
  conceptKind,
  relationPicker,
  placeholder,
  className = '',
  disabled = false,
  block = false,
}) => {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<CatalogSearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [showResults, setShowResults] = useState(false);
  const [browseOpen, setBrowseOpen] = useState(false);

  const inputWrapRef = useRef<HTMLDivElement>(null);
  const blurTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [popperRect, setPopperRect] = useState<DOMRect | null>(null);

  // Memoize so the array reference is stable across renders — this list is a
  // useEffect dependency (inline search) AND a prop to the browse modal (whose
  // own effects depend on it). Recomputing it every render (`.map(String)`)
  // would trigger an infinite render loop (setState in effect → re-render → new
  // array → effect re-runs → …).
  const allowedList = useMemo(
    () => (allowedTypes ? allowedTypes.map(String) : undefined),
    [allowedTypes],
  );
  const relationGroups = relationPicker?.options ?? RELATION_OPTION_GROUPS;
  const defaultRelation = relationPicker?.defaultRelation ?? DEFAULT_RELATION;

  // Portal the results popover out of scroll containers so it isn't clipped.
  // Reposition on scroll/resize (capture-phase catches nested scroll parents).
  useLayoutEffect(() => {
    if (!showResults) {
      setPopperRect(null);
      return;
    }
    const update = () => {
      if (inputWrapRef.current) {
        setPopperRect(inputWrapRef.current.getBoundingClientRect());
      }
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [showResults]);

  // Debounced cross-catalog type-ahead.
  useEffect(() => {
    if (!query || query.length < 2) {
      setResults([]);
      return;
    }
    let cancelled = false;
    setSearching(true);
    const handle = setTimeout(async () => {
      try {
        // Push type + kind filtering server-side: the backend applies the
        // per-type-floor guarantee to the allowed types and ignores kind for
        // non-concept catalogs. This is both more efficient and more correct
        // than fetching all and filtering client-side.
        const resp = await searchCatalogs(query, {
          limit: 10,
          types: allowedList?.length ? allowedList.join(',') : undefined,
          kind: conceptKind,
        });
        if (!cancelled) setResults(resp.results);
      } catch {
        if (!cancelled) setResults([]);
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [query, allowedList, conceptKind]);

  /** Build a CatalogSelection from a search hit / browser item + catalog type. */
  const toSelection = useCallback(
    (id: string, label: string, type: string): CatalogSelection => ({
      type,
      id,
      label,
      ...(relationPicker ? { relation: defaultRelation } : {}),
    }),
    [relationPicker, defaultRelation],
  );

  const addSelection = useCallback(
    (sel: CatalogSelection) => {
      const key = selectionKey(sel);
      if (mode === 'single') {
        // Replace (but avoid a no-op set so chips don't re-mount needlessly).
        if (value.length === 1 && selectionKey(value[0]) === key) return;
        onChange([sel]);
      } else {
        if (value.some((s) => selectionKey(s) === key)) return;
        onChange([...value, sel]);
      }
    },
    [mode, value, onChange],
  );

  const removeAt = useCallback(
    (idx: number) => {
      onChange(value.filter((_, i) => i !== idx));
    },
    [value, onChange],
  );

  const setRelationAt = useCallback(
    (idx: number, relation: string) => {
      onChange(
        value.map((s, i) => (i === idx ? { ...s, relation } : s)),
      );
    },
    [value, onChange],
  );

  /** Browse-modal toggle: a CatalogItem carries name/id but not its catalog
   *  type, so the modal passes the resolved type alongside. */
  const handleBrowseToggle = useCallback(
    (item: CatalogItem, catalogType: string) => {
      const sel = toSelection(
        String(item.id),
        String(item.name ?? item.slug ?? item.id),
        catalogType,
      );
      // In single mode, picking in the modal also closes it.
      if (mode === 'single') {
        addSelection(sel);
        setBrowseOpen(false);
      } else {
        // Toggle: remove if already picked.
        const key = selectionKey(sel);
        const existing = value.findIndex((s) => selectionKey(s) === key);
        if (existing >= 0) removeAt(existing);
        else addSelection(sel);
      }
    },
    [mode, toSelection, addSelection, removeAt, value],
  );

  const placeholderText =
    placeholder ??
    t('catalogs.picker_placeholder', 'Search any catalog…');

  return (
    <div className={`flex flex-col gap-2 ${className}`}>
      {/* Input + browse button */}
      <div ref={inputWrapRef} className={`relative ${block ? 'w-full' : ''}`}>
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400 pointer-events-none" />
        <input
          type="text"
          value={query}
          disabled={disabled}
          onChange={(e) => {
            setQuery(e.target.value);
            setShowResults(true);
          }}
          onFocus={() => setShowResults(true)}
          onBlur={() => {
            blurTimer.current = setTimeout(() => setShowResults(false), 150);
          }}
          placeholder={placeholderText}
          className={`w-full pl-8 pr-9 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 focus:ring-2 focus:ring-blue-500 outline-none disabled:opacity-50 ${
            block ? 'pr-20' : ''
          }`}
        />
        {/* Browse button (always inside the input, right side). */}
        <button
          type="button"
          onClick={() => setBrowseOpen(true)}
          disabled={disabled}
          title={t('catalogs.picker_browse', 'Browse catalog')}
          className="absolute right-1.5 top-1/2 -translate-y-1/2 inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-md text-gray-500 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 disabled:opacity-50"
        >
          <LayoutGrid className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">{t('catalogs.picker_browse', 'Browse')}</span>
        </button>

        {/* Inline results popover */}
        {showResults && (query.length >= 2 || results.length > 0) && popperRect && (
          <Portal>
            <div
              className="fixed z-popover max-h-64 overflow-auto rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg"
              style={{ top: popperRect.bottom + 4, left: popperRect.left, width: popperRect.width }}
            >
              {searching && results.length === 0 ? (
                <p className="px-3 py-2 text-xs text-gray-400">{t('common.searching', 'Searching…')}</p>
              ) : results.length === 0 ? (
                <p className="px-3 py-2 text-xs text-gray-400">
                  {t('catalogs.edge_no_results', 'No matches.')}
                </p>
              ) : (
                results.map((r) => (
                  <button
                    key={`${r.type}:${r.id}`}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => {
                      addSelection(toSelection(r.id, r.label, r.type));
                      setQuery('');
                      setResults([]);
                      setShowResults(false);
                      if (blurTimer.current) clearTimeout(blurTimer.current);
                    }}
                    className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-blue-50 dark:hover:bg-blue-900/20"
                  >
                    <span className="text-[10px] font-bold uppercase tracking-wide text-blue-500 w-20 shrink-0">
                      {r.type}
                    </span>
                    <span className="truncate">{r.label}</span>
                  </button>
                ))
              )}
            </div>
          </Portal>
        )}
      </div>

      {/* Selected chips */}
      {value.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {value.map((sel, idx) => (
            <li
              key={`${sel.type}:${sel.id}:${sel.relation ?? ''}:${idx}`}
              className="group flex items-center gap-1.5 rounded-full border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 pl-2.5 pr-1 py-1 text-xs"
            >
              {relationPicker ? (
                <RelationTypeSelect
                  value={sel.relation ?? defaultRelation}
                  onChange={(r) => setRelationAt(idx, r)}
                  options={relationGroups}
                />
              ) : (
                <span className="text-[10px] font-bold uppercase tracking-wide text-blue-500 shrink-0">
                  {sel.type}
                </span>
              )}
              <span className="text-gray-700 dark:text-gray-200 truncate max-w-[14rem]">
                {sel.label}
              </span>
              <button
                type="button"
                onClick={() => removeAt(idx)}
                className="ml-0.5 p-0.5 rounded-full text-gray-400 hover:bg-red-100 hover:text-red-600 dark:hover:bg-red-900/30"
                title={t('common.remove', 'Remove')}
              >
                <X className="w-3 h-3" />
              </button>
            </li>
          ))}
        </ul>
      )}

      <CatalogPickerBrowseModal
        isOpen={browseOpen}
        onClose={() => setBrowseOpen(false)}
        picked={value}
        onTogglePick={handleBrowseToggle}
        allowedTypes={allowedList}
        conceptKind={conceptKind}
        mode={mode}
      />
    </div>
  );
};
