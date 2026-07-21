/**
 * RelationTypeSelect — a modern, custom dropdown for picking a relation type
 * (TREATS / AFFECTS / …).
 *
 * Pulls its data from the backend single source of truth
 * (``GET /catalogs/relation-types`` → ``app/catalogs/relation_types.py``), which
 * carries the label, an icon (lucide name, rendered as SVG via ``DynamicIcon``),
 * the grouping, and a one-line description ("when to use this"). Falls back to
 * the bundled ``catalogRelationTypes`` groups if the fetch hasn't resolved.
 *
 * Renders a Portaled, searchable, grouped popover (``z-popover`` > ``z-modal``)
 * with keyboard navigation and an obvious affordance (chevron + hover/active
 * states). Used inside {@link CatalogItemPicker} chips.
 */
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, Check, Search, Info } from 'lucide-react';
import { Portal } from '../ui/Portal';
import { DynamicIcon } from '../ui/DynamicIcon';
import {
  getRelationType,
  getRelationTypes,
  loadRelationTypes,
} from '../../services/catalogService';
import type { RelationOptionGroup } from './catalogRelationTypes';

interface RelationTypeSelectProps {
  value: string;
  onChange: (value: string) => void;
  /** Bundled fallback groups (used until the backend metadata loads). */
  options: RelationOptionGroup[];
  className?: string;
  /** Compact trigger styling for use inside chips (default true). */
  compact?: boolean;
}

export const RelationTypeSelect: React.FC<RelationTypeSelectProps> = ({
  value,
  onChange,
  options,
  className = '',
  compact = true,
}) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [activeIdx, setActiveIdx] = useState(0);
  const [rect, setRect] = useState<DOMRect | null>(null);
  // Whether the popover renders above ('top') or below ('bottom') the trigger.
  // Flipped to 'top' when there's no room below the trigger (e.g. a chip near
  // the viewport/modal bottom) so the dropdown is never clipped.
  const [placement, setPlacement] = useState<'top' | 'bottom'>('bottom');
  // Re-render once the async metadata cache resolves.
  const [, setMetaTick] = useState(0);

  const triggerRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);

  // Kick off the one-shot metadata fetch (cached session-wide). Tick state so
  // the grouped list rebuilds when data lands.
  useEffect(() => {
    let mounted = true;
    loadRelationTypes().then(() => {
      if (mounted) setMetaTick((n) => n + 1);
    });
    return () => {
      mounted = false;
    };
  }, []);

  // Reposition the Portaled popover on scroll/resize (capture-phase catches
  // the modal's overflow container, whose scroll events don't bubble). Also
  // picks placement ('top'/'bottom') based on available space so the dropdown
  // is never clipped by the viewport/modal bottom edge.
  useLayoutEffect(() => {
    if (!open) {
      setRect(null);
      return;
    }
    const update = () => {
      if (!triggerRef.current) return;
      const r = triggerRef.current.getBoundingClientRect();
      setRect(r);
      // Estimate the popover's max height: search header (~45px) + the
      // `max-h-72` (288px) scrollable list + padding. Overestimating slightly
      // is safe — it only makes us flip sooner when space is tight.
      const POPOVER_H = 340;
      const spaceBelow = window.innerHeight - r.bottom;
      const spaceAbove = r.top;
      setPlacement(
        spaceBelow < POPOVER_H + 8 && spaceAbove > spaceBelow ? 'top' : 'bottom',
      );
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [open]);

  // Click-outside + Escape close.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (
        triggerRef.current?.contains(target) ||
        popRef.current?.contains(target)
      ) {
        return;
      }
      close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  // Prefer backend metadata; fall back to the bundled groups until it loads.
  const groups = useMemo<RelationOptionGroup[]>(() => {
    const items = getRelationTypes();
    if (items.length) {
      const map = new Map<string, string[]>();
      for (const it of items) {
        if (!map.has(it.group)) map.set(it.group, []);
        map.get(it.group)!.push(it.value);
      }
      return Array.from(map.entries()).map(([group, values]) => ({ group, values }));
    }
    return options;
  }, [options]);

  // Filtered, flattened value list (for rendering + keyboard nav).
  const filteredGroups = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return groups;
    // Match against the backend label/description too (not just the wire value).
    return groups
      .map((g) => ({
        ...g,
        values: g.values.filter((v) => {
          const meta = getRelationType(v);
          const hay = `${v} ${meta?.label ?? ''} ${meta?.description ?? ''}`.toLowerCase();
          return hay.includes(q);
        }),
      }))
      .filter((g) => g.values.length > 0);
  }, [groups, query]);

  const flat = useMemo(
    () => filteredGroups.flatMap((g) => g.values),
    [filteredGroups],
  );

  useEffect(() => {
    if (activeIdx >= flat.length) setActiveIdx(0);
  }, [flat, activeIdx]);

  // Seed the active index to the current value on open.
  useEffect(() => {
    if (!open) return;
    setQuery('');
    const i = flat.indexOf(value);
    setActiveIdx(i >= 0 ? i : 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const close = () => {
    setOpen(false);
    setQuery('');
  };

  const pick = (v: string) => {
    onChange(v);
    close();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setOpen(true);
      setActiveIdx((i) => Math.min(i + 1, flat.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setOpen(true);
      setActiveIdx((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter' && open) {
      e.preventDefault();
      const v = flat[activeIdx];
      if (v) pick(v);
    }
  };

  const selectedMeta = getRelationType(value);
  const label = selectedMeta?.label ?? value.replace(/_/g, ' ');

  return (
    <div className={`relative ${className}`}>
      <button
        ref={triggerRef}
        type="button"
        onMouseDown={(e) => e.stopPropagation()}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={onKeyDown}
        className={
          compact
            ? 'inline-flex items-center gap-1 bg-blue-50 dark:bg-blue-900/30 hover:bg-blue-100 dark:hover:bg-blue-900/50 font-semibold text-blue-700 dark:text-blue-300 rounded-md pl-1.5 pr-1 py-0.5 text-[11px] outline-none focus:ring-2 focus:ring-blue-500/40 transition-colors'
            : 'inline-flex items-center gap-2 w-full bg-white dark:bg-gray-900 hover:bg-gray-50 dark:hover:bg-gray-800 font-medium text-gray-700 dark:text-gray-200 rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500/40 transition-colors'
        }
        title={selectedMeta?.description ?? t('catalogs.picker_relation', 'Relation')}
      >
        {selectedMeta?.icon && (
          <DynamicIcon
            icon={selectedMeta.icon}
            className={compact ? 'w-3 h-3 shrink-0' : 'w-4 h-4 shrink-0'}
          />
        )}
        <span className="capitalize truncate">{label}</span>
        <ChevronDown
          className={`${compact ? 'w-3 h-3' : 'w-4 h-4'} text-blue-500 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && rect && (
        <Portal>
          <div
            ref={popRef}
            className={`fixed z-popover w-72 max-w-[85vw] rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg animate-in fade-in ${placement === 'top' ? 'slide-in-from-bottom-1' : 'slide-in-from-top-1'} duration-150`}
            style={
              placement === 'top'
                ? // Anchor to the trigger's top edge and grow upward. Using
                  // `bottom` (distance from viewport bottom) avoids needing
                  // the popover's measured height.
                  { bottom: window.innerHeight - rect.top + 4, left: rect.left }
                : { top: rect.bottom + 4, left: rect.left }
            }
          >
            <div className="relative p-2 border-b border-gray-100 dark:border-gray-700">
              <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
              <input
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={onKeyDown}
                placeholder={t('catalogs.picker_relation_filter', 'Filter relations…')}
                className="w-full pl-7 pr-3 py-1.5 text-xs rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 focus:ring-2 focus:ring-blue-500 outline-none"
              />
            </div>
            <div className="max-h-72 overflow-auto custom-scrollbar py-1">
              {filteredGroups.length === 0 ? (
                <p className="px-3 py-2 text-xs text-gray-400">
                  {t('catalogs.edge_no_results', 'No matches.')}
                </p>
              ) : (
                filteredGroups.map((grp) => (
                  <div key={grp.group}>
                    <p className="px-3 pt-2 pb-1 text-[10px] font-bold uppercase tracking-wider text-gray-400">
                      {grp.group}
                    </p>
                    {grp.values.map((v) => {
                      const isActive = flat[activeIdx] === v;
                      const isSelected = v === value;
                      const meta = getRelationType(v);
                      return (
                        <button
                          key={v}
                          type="button"
                          onMouseEnter={() => setActiveIdx(flat.indexOf(v))}
                          onClick={() => pick(v)}
                          className={`group/row flex w-full items-start gap-2 px-3 py-1.5 text-left transition-colors ${
                            isActive
                              ? 'bg-blue-50 dark:bg-blue-900/30'
                              : 'hover:bg-gray-50 dark:hover:bg-gray-700/50'
                          }`}
                        >
                          {meta?.icon ? (
                            <DynamicIcon
                              icon={meta.icon}
                              className="w-3.5 h-3.5 mt-0.5 shrink-0 text-gray-500 dark:text-gray-400"
                            />
                          ) : (
                            <span className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                          )}
                          <span className="flex-1 min-w-0">
                            <span
                              className={`flex items-center gap-1 text-xs capitalize ${isActive ? 'text-blue-700 dark:text-blue-300 font-medium' : 'text-gray-700 dark:text-gray-200'}`}
                            >
                              <span className="truncate">
                                {meta?.label ?? v.replace(/_/g, ' ')}
                              </span>
                              {meta?.description && (
                                <span
                                  className="relative inline-flex shrink-0"
                                  title={meta.description}
                                >
                                  <Info className="w-3 h-3 text-gray-300 group-hover/row:text-blue-500" />
                                </span>
                              )}
                              {isSelected && (
                                <Check className="w-3.5 h-3.5 text-blue-600 dark:text-blue-400 shrink-0 ml-auto" />
                              )}
                            </span>
                            {meta?.description && (
                              <span className="block text-[11px] text-gray-400 dark:text-gray-500 leading-snug mt-0.5 line-clamp-2">
                                {meta.description}
                              </span>
                            )}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                ))
              )}
            </div>
          </div>
        </Portal>
      )}
    </div>
  );
};
