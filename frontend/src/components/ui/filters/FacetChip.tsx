import React, { useMemo, useState } from 'react';
import { Grid, ChevronDown, Check, X, SlidersHorizontal } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { DynamicIcon } from '../DynamicIcon';
import { Popover, PopoverTrigger, PopoverContent, SearchInput } from '@neuronection/assistant-ui';
import type { FacetDefinition, FacetOption, FilterValue } from './types';
import { isDefaultValue, defaultFilterValue } from './useFilterState';
import { formatNumber } from '../../../lib/dateFormats';

export interface FacetChipProps<T> {
  facet: FacetDefinition<T>;
  value: FilterValue;
  options: FacetOption[];
  /** Full value replacement (used by toggle-kind chips). */
  onValueChange: (value: FilterValue) => void;
  /** Toggle a single option value (used by single/multi chips). */
  onToggleOption: (optionValue: string) => void;
  /** Show the removable active-value pills row (desktop). Defaults to true. */
  showActivePills?: boolean;
}

/** Render a facet icon — DynamicIcon for strings/IconConfigs, raw node otherwise. */
function renderFacetIcon(icon: FacetOption['icon'], className: string) {
  if (icon === undefined || icon === null) return null;
  if (React.isValidElement(icon)) return icon;
  return <DynamicIcon icon={icon as string | { type: 'lucide' | 'custom_svg'; value: string }} className={className} />;
}

const TRIGGER = [
  'flex items-center justify-between gap-2 whitespace-nowrap rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors',
  'focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--as-focus-ring)]',
].join(' ');

const TRIGGER_INACTIVE =
  'border-[var(--as-border)] bg-[var(--as-surface)] text-[var(--as-fg)] hover:border-[color-mix(in_srgb,var(--as-primary)_40%,var(--as-border))]';

const TRIGGER_ACTIVE =
  'border-[var(--as-primary)] bg-[color-mix(in_srgb,var(--as-primary)_12%,transparent)] text-[var(--as-primary)]';

export const FacetChip = <T,>({
  facet,
  value,
  options,
  onValueChange,
  onToggleOption,
  showActivePills = true,
}: FacetChipProps<T>) => {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState('');

  const label = facet.i18nKey ? t(facet.i18nKey, { defaultValue: facet.label }) : facet.label;
  // Resolve undefined (facet not yet in state — happens briefly when the
  // facets array changes before the hook's sync effect runs) to the default.
  const resolvedValue: FilterValue = value ?? defaultFilterValue(facet);
  const active = !isDefaultValue(facet, resolvedValue);

  const filteredOptions = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return options;
    return options.filter((option) => option.label.toLowerCase().includes(needle));
  }, [options, query]);

  const selected =
    resolvedValue.kind === 'multi'
      ? resolvedValue.values
      : resolvedValue.kind === 'single'
        ? resolvedValue.value
          ? [resolvedValue.value]
          : []
        : [];

  const triggerText = (() => {
    if (resolvedValue.kind === 'multi') {
      if (selected.length === 0) return null;
      if (selected.length === 1) {
        return options.find((o) => o.value === selected[0])?.label ?? selected[0];
      }
      return t('filters.selected_count', { count: selected.length });
    }
    if (resolvedValue.kind === 'single') {
      if (!selected.length) return null;
      return options.find((o) => o.value === selected[0])?.label ?? selected[0];
    }
    return null;
  })();

  // --- Toggle-kind: a self-contained switch chip (no popover) -------------
  if (facet.kind === 'toggle') {
    if (resolvedValue.kind !== 'toggle') return null;
    return (
      <button
        type="button"
        aria-pressed={resolvedValue.on}
        onClick={() => onValueChange({ kind: 'toggle', on: !resolvedValue.on })}
        className={`${TRIGGER} ${active ? TRIGGER_ACTIVE : TRIGGER_INACTIVE}`}
      >
        {facet.icon ? (
          renderFacetIcon(facet.icon, 'size-4')
        ) : (
          <SlidersHorizontal className="size-4" aria-hidden />
        )}
        <span>{label}</span>
      </button>
    );
  }

  return (
    <div className="flex min-w-0 items-center gap-2">
      <Popover
        open={isOpen}
        onOpenChange={(next) => {
          setIsOpen(next);
          if (!next) setQuery('');
        }}
      >
        <PopoverTrigger asChild>
          <button type="button" className={`${TRIGGER} ${active ? TRIGGER_ACTIVE : TRIGGER_INACTIVE}`}>
            <span className="flex items-center gap-2">
              {facet.icon ? (
                renderFacetIcon(facet.icon, 'size-4')
              ) : (
                <Grid className="size-4 opacity-70" aria-hidden />
              )}
              <span>{triggerText ?? label}</span>
            </span>
            <ChevronDown
              className={`size-4 opacity-60 transition-transform ${isOpen ? 'rotate-180' : ''}`}
              aria-hidden
            />
          </button>
        </PopoverTrigger>

        <PopoverContent
          align="start"
          sideOffset={8}
          className="w-64 overflow-hidden rounded-[var(--as-radius-lg)] border border-[var(--as-border)] bg-[var(--as-surface-raised)] p-0 shadow-[var(--as-shadow-pop)]"
          onOpenAutoFocus={(event) => event.preventDefault()}
        >
          {options.length > 8 && (
            <div className="border-b border-[var(--as-border)] p-2">
              <SearchInput
                value={query}
                onChange={setQuery}
                placeholder={t('filters.search_options_placeholder')}
                ariaLabel={t('filters.search_options')}
                autoFocus
              />
            </div>
          )}

          <div className="max-h-[280px] overflow-y-auto p-1">
            {filteredOptions.length === 0 ? (
              <div className="px-3 py-3 text-sm text-[var(--as-muted-fg)]">
                {options.length === 0 ? t('common.no_options') : t('filters.no_matches')}
              </div>
            ) : (
              filteredOptions.map((opt) => {
                const isSelected = selected.includes(opt.value);
                return (
                  <button
                    key={opt.value}
                    type="button"
                    role={facet.kind === 'single' ? 'menuitemradio' : 'menuitemcheckbox'}
                    aria-checked={isSelected}
                    onClick={() => onToggleOption(opt.value)}
                    className={`flex w-full items-center justify-between gap-3 rounded-[var(--as-radius-sm)] px-3 py-2 text-left text-sm transition-colors ${
                      isSelected
                        ? 'bg-[color-mix(in_srgb,var(--as-primary)_10%,transparent)] font-medium text-[var(--as-primary)]'
                        : 'text-[var(--as-fg)] hover:bg-[var(--as-secondary)]'
                    }`}
                  >
                    <span className="flex min-w-0 items-center gap-2.5">
                      {opt.icon ? renderFacetIcon(opt.icon, 'size-4 shrink-0') : null}
                      <span className="truncate">{opt.label}</span>
                    </span>
                    <span className="flex shrink-0 items-center gap-2">
                      {opt.count !== undefined && (
                        <span className="rounded-full bg-[var(--as-secondary)] px-2 py-0.5 text-[10px] font-semibold tabular-nums text-[var(--as-muted-fg)]">
                          {formatNumber(opt.count)}
                        </span>
                      )}
                      {isSelected && <Check className="size-4 text-[var(--as-primary)]" aria-hidden />}
                    </span>
                  </button>
                );
              })
            )}
          </div>
        </PopoverContent>
      </Popover>

      {/* Active value pills */}
      {showActivePills && selected.length > 0 && (
        <div className="flex max-w-[400px] flex-wrap items-center gap-1.5 overflow-hidden">
          {selected.map((sel) => {
            const opt = options.find((o) => o.value === sel);
            if (!opt) return null;
            return (
              <span
                key={sel}
                className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-[color-mix(in_srgb,var(--as-primary)_12%,transparent)] py-1 pl-2.5 pr-1 text-[11px] font-medium text-[var(--as-primary)]"
              >
                {opt.icon && renderFacetIcon(opt.icon, 'size-3')}
                <span>{opt.label}</span>
                <button
                  type="button"
                  onClick={() => onToggleOption(sel)}
                  className="rounded-full p-1 transition-colors hover:bg-[color-mix(in_srgb,var(--as-primary)_18%,transparent)] focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--as-focus-ring)]"
                  aria-label={t('common.remove_named', { name: opt.label })}
                >
                  <X className="size-3" aria-hidden />
                </button>
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
};
