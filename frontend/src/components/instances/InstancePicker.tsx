/**
 * InstancePicker — reusable search-and-select for **patient-scoped clinical
 * records** (examinations, medications, observations, documents, events,
 * allergies, vaccines). The instance counterpart of `CatalogItemPicker`.
 *
 * Two discovery paths feed one controlled selection (mirrors the catalog picker):
 *   1. **Inline type-ahead** — debounced; calls the active adapter's `search()`
 *      (single type) or the optional `unifiedSearch` (multiple types). Results
 *      render as `<TYPE-chip> <label> <date>`.
 *   2. **Browse button** (grid icon in the input) — opens
 *      {@link InstanceBrowseModal} with the full per-type filterable list.
 *
 * Selection model:
 *   - `mode: 'single'` → picking replaces the selection (length ≤ 1).
 *   - `mode: 'multi'`  → appends; deduped by `type:id` (+ `relation`).
 *
 * Patient scope: defaults to the current patient context
 * (`usePatientStore.currentPatient?.id`). Pass `patientId` to override.
 *
 * Relation binding: pass `relationPicker` to surface a relation-type dropdown
 * on each selected chip (reuses the catalog `RelationTypeSelect` — imported,
 * not duplicated — so relation vocabularies stay consistent across the app).
 *
 * Controlled: `value: InstanceSelection[]` + `onChange`. Selected chips render
 * below the input with a remove (X) each.
 *
 * See `dev/plans/instance-browser-unified-picker-2026-07-16.md`.
 */
import React, { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Search, X, LayoutGrid } from 'lucide-react';
import { Portal } from '../ui/Portal';
import { RelationTypeSelect } from '../catalog/RelationTypeSelect';
import {
  DEFAULT_RELATION,
  RELATION_OPTION_GROUPS,
  type RelationOptionGroup,
} from '../catalog/catalogRelationTypes';
import { usePatientStore } from '../../store/slices/patientSlice';
import { getAdapters } from './instanceRegistry';
import { InstanceBrowseModal } from './InstanceBrowseModal';
import { InstanceCard } from './InstanceCard';
import type {
  InstanceAdapter,
  InstanceQuery,
  InstanceSearchHit,
  InstanceSelection,
  InstanceType,
} from './types';

export interface InstancePickerProps {
  /** Always an array; in `single` mode length is 0 or 1. */
  value: InstanceSelection[];
  onChange: (next: InstanceSelection[]) => void;
  mode?: 'single' | 'multi';
  /** Restrict to a subset of entity types; default = all registered. */
  allowedTypes?: InstanceType[];
  /**
   * Patient scope. Defaults to the current patient context. Required for
   * patient-scoped adapters (the secure default).
   */
  patientId?: string;
  /**
   * Cross-type search callback (Phase 2: backend `/instances/search`). When
   * supplied, the inline type-ahead and browse modal support multiple types.
   * Without it, multi-type inline search is disabled (browse-only).
   */
  unifiedSearch?: (query: InstanceQuery) => Promise<InstanceSearchHit[]>;
  /** Enable relation binding (chips get a relation dropdown). */
  relationPicker?: {
    options?: RelationOptionGroup[];
    defaultRelation?: string;
  };
  placeholder?: string;
  className?: string;
  disabled?: boolean;
  /** Full-width block input (useful inside form rows). */
  block?: boolean;
  /**
   * How selected items render below the input:
   *   - `'chips'` (default) — compact label chips (the original behavior).
   *   - `'cards'` — rich {@link InstanceCard} per item (fetches each record
   *     via the adapter so date/status/badges show). Use when a linked record
   *     should be scannable at a glance (e.g. an examination attached to a
   *     medication).
   */
  displayMode?: 'chips' | 'cards';
  /**
   * Cards-mode only: render extra content inside each selected card's footer
   * (e.g. a per-link "reason" / "notes" text input). Receives the selection
   * + its index so the caller can bind to its own per-link state. The picker
   * owns selection (add/remove); the caller owns the per-link extras.
   */
  renderCardFooter?: (
    selection: InstanceSelection,
    idx: number,
  ) => React.ReactNode;
}

/** Dedup key — includes relation so the same record can be bound two ways. */
export const selectionKey = (s: InstanceSelection): string =>
  `${s.type}:${s.id}${s.relation ? `:${s.relation}` : ''}`;

/** Fallback search when an adapter doesn't override `search()`: fetch + project. */
async function defaultAdapterSearch(
  adapter: InstanceAdapter<unknown>,
  query: InstanceQuery,
): Promise<InstanceSearchHit[]> {
  const result = await adapter.fetch(query);
  return result.items.map((it) => {
    const row = adapter.toRow(it);
    return {
      type: row.type,
      id: row.id,
      label: row.label,
      subtitle: row.subtitle,
      date: row.date,
    };
  });
}

export const InstancePicker: React.FC<InstancePickerProps> = ({
  value,
  onChange,
  mode = 'multi',
  allowedTypes,
  patientId,
  unifiedSearch,
  relationPicker,
  placeholder,
  className = '',
  disabled = false,
  block = false,
  displayMode = 'chips',
  renderCardFooter,
}) => {
  const { t } = useTranslation();
  const currentPatientId = usePatientStore((s) => s.currentPatient?.id);
  const scopePatientId = patientId ?? currentPatientId;

  const adapters = useMemo(() => getAdapters(allowedTypes), [allowedTypes]);
  const isMultiType = adapters.length > 1;
  const singleAdapter = adapters.length === 1 ? adapters[0] : null;

  const [query, setQuery] = useState('');
  const [results, setResults] = useState<InstanceSearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [showResults, setShowResults] = useState(false);
  const [browseOpen, setBrowseOpen] = useState(false);

  const inputWrapRef = useRef<HTMLDivElement | null>(null);
  const blurTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [popperRect, setPopperRect] = useState<DOMRect | null>(null);

  const relationGroups = relationPicker?.options ?? RELATION_OPTION_GROUPS;
  const defaultRelation = relationPicker?.defaultRelation ?? DEFAULT_RELATION;

  // Portal the results popover out of scroll containers; reposition on scroll/resize.
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

  // Debounced inline type-ahead. Single type → adapter.search; multi-type →
  // unifiedSearch (only when provided). Without a path, results stay empty and
  // the user is guided to the Browse button.
  React.useEffect(() => {
    if (query.length < 2) {
      setResults([]);
      return;
    }
    let cancelled = false;
    setSearching(true);
    const handle = setTimeout(async () => {
      try {
        const q: InstanceQuery = {
          patientId: scopePatientId,
          q: query,
          limit: 10,
          offset: 0,
          serverParams: {},
        };
        let hits: InstanceSearchHit[] = [];
        if (singleAdapter) {
          hits = singleAdapter.search
            ? await singleAdapter.search(q)
            : await defaultAdapterSearch(singleAdapter, q);
        } else if (isMultiType && unifiedSearch) {
          hits = await unifiedSearch(q);
        }
        if (!cancelled) setResults(hits);
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
  }, [query, singleAdapter, isMultiType, unifiedSearch, scopePatientId]);

  const toSelection = useCallback(
    (hit: InstanceSearchHit): InstanceSelection => ({
      type: hit.type,
      id: hit.id,
      label: hit.label,
      subtitle: hit.subtitle,
      ...(relationPicker ? { relation: defaultRelation } : {}),
    }),
    [relationPicker, defaultRelation],
  );

  const addSelection = useCallback(
    (sel: InstanceSelection) => {
      const key = selectionKey(sel);
      if (mode === 'single') {
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
    (idx: number) => onChange(value.filter((_, i) => i !== idx)),
    [value, onChange],
  );

  const setRelationAt = useCallback(
    (idx: number, relation: string) =>
      onChange(value.map((s, i) => (i === idx ? { ...s, relation } : s))),
    [value, onChange],
  );

  /** Browse-modal toggle: a row already carries its type. */
  const handleBrowseToggle = useCallback(
    (row: { id: string; type: InstanceType; label?: string; subtitle?: string }) => {
      const sel: InstanceSelection = {
        type: row.type,
        id: row.id,
        label: row.label,
        subtitle: row.subtitle,
        ...(relationPicker ? { relation: defaultRelation } : {}),
      };
      if (mode === 'single') {
        addSelection(sel);
        setBrowseOpen(false);
      } else {
        const key = selectionKey(sel);
        const existing = value.findIndex((s) => selectionKey(s) === key);
        if (existing >= 0) removeAt(existing);
        else addSelection(sel);
      }
    },
    [mode, relationPicker, defaultRelation, addSelection, removeAt, value],
  );

  const inlineSearchEnabled = !!singleAdapter || (isMultiType && !!unifiedSearch);
  const placeholderText =
    placeholder ??
    (inlineSearchEnabled
      ? t('instances.picker_placeholder', 'Search records…')
      : t('instances.picker_placeholder_browse', 'Browse records…'));

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
          className={`w-full pl-8 pr-20 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 focus:ring-2 focus:ring-blue-500 outline-none disabled:opacity-50`}
        />
        {/* Browse button */}
        <button
          type="button"
          onClick={() => setBrowseOpen(true)}
          disabled={disabled}
          title={t('instances.picker_browse', 'Browse records')}
          className="absolute right-1.5 top-1/2 -translate-y-1/2 inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-md text-gray-500 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 disabled:opacity-50"
        >
          <LayoutGrid className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">
            {t('instances.picker_browse', 'Browse')}
          </span>
        </button>

        {/* Inline results popover */}
        {showResults && inlineSearchEnabled && query.length >= 2 && popperRect && (
          <Portal>
            <div
              className="fixed z-popover max-h-64 overflow-auto rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg"
              style={{
                top: popperRect.bottom + 4,
                left: popperRect.left,
                width: popperRect.width,
              }}
            >
              {searching && results.length === 0 ? (
                <p className="px-3 py-2 text-xs text-gray-400">
                  {t('common.searching', 'Searching…')}
                </p>
              ) : results.length === 0 ? (
                <p className="px-3 py-2 text-xs text-gray-400">
                  {t('instances.no_results', 'No matches.')}
                </p>
              ) : (
                results.map((r) => (
                  <button
                    key={`${r.type}:${r.id}`}
                    type="button"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => {
                      addSelection(toSelection(r));
                      setQuery('');
                      setResults([]);
                      setShowResults(false);
                      if (blurTimer.current) clearTimeout(blurTimer.current);
                    }}
                    className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-blue-50 dark:hover:bg-blue-900/20"
                  >
                    <span className="text-[10px] font-bold uppercase tracking-wide text-blue-500 w-20 shrink-0">
                      {t(`instances.type_${r.type}`, r.type)}
                    </span>
                    <span className="truncate flex-1">{r.label}</span>
                    {r.subtitle && (
                      <span className="text-[10px] text-gray-400 truncate max-w-[10rem]">
                        {r.subtitle}
                      </span>
                    )}
                  </button>
                ))
              )}
            </div>
          </Portal>
        )}
      </div>

      {/* Selected items */}
      {value.length > 0 && displayMode === 'cards' && (
        <div className="flex flex-col gap-2">
          {value.map((sel, idx) => (
            <InstanceCard
              key={`${sel.type}:${sel.id}:${sel.relation ?? ''}:${idx}`}
              selection={sel}
              patientId={scopePatientId}
              onRemove={() => removeAt(idx)}
              footer={renderCardFooter?.(sel, idx)}
              actions={
                relationPicker ? (
                  <RelationTypeSelect
                    value={sel.relation ?? defaultRelation}
                    onChange={(r) => setRelationAt(idx, r)}
                    options={relationGroups}
                  />
                ) : undefined
              }
            />
          ))}
        </div>
      )}

      {value.length > 0 && displayMode === 'chips' && (
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
                  {t(`instances.type_${sel.type}`, sel.type)}
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

      <InstanceBrowseModal
        isOpen={browseOpen}
        onClose={() => setBrowseOpen(false)}
        picked={value}
        onTogglePick={handleBrowseToggle}
        allowedTypes={allowedTypes}
        patientId={scopePatientId}
        mode={mode}
        unifiedSearch={unifiedSearch}
      />
    </div>
  );
};
