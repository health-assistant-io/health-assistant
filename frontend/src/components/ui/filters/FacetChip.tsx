import React, { useRef, useState } from 'react';
import { Grid, ChevronDown, CheckCircle2, X, SlidersHorizontal } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { DynamicIcon } from '../DynamicIcon';
import { Popover } from '../Popover';
import type { FacetDefinition, FacetOption, FilterValue } from './types';
import { isDefaultValue, defaultFilterValue } from './useFilterState';

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

function optionLabel<T>(facet: FacetDefinition<T>, value: FilterValue, options: FacetOption[]): string | null {
  if (facet.kind === 'multi' && value.kind === 'multi') {
    if (value.values.length === 0) return null;
    if (value.values.length === 1) {
      return options.find((o) => o.value === value.values[0])?.label ?? value.values[0];
    }
    return `${value.values.length} selected`;
  }
  if (facet.kind === 'single' && value.kind === 'single') {
    if (!value.value) return null;
    return options.find((o) => o.value === value.value)?.label ?? value.value;
  }
  return null;
}

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
  const triggerRef = useRef<HTMLButtonElement>(null);

  const label = facet.i18nKey ? t(facet.i18nKey, { defaultValue: facet.label }) : facet.label;
  // Resolve undefined (facet not yet in state — happens briefly when the
  // facets array changes before the hook's sync effect runs) to the default.
  const resolvedValue: FilterValue = value ?? defaultFilterValue(facet);
  const active = !isDefaultValue(facet, resolvedValue);

  // --- Toggle-kind: a self-contained switch chip (no popover) -------------
  if (facet.kind === 'toggle') {
    if (resolvedValue.kind !== 'toggle') return null;
    return (
      <button
        type="button"
        onClick={() => onValueChange({ kind: 'toggle', on: !resolvedValue.on })}
        className={`flex items-center gap-2 px-3 py-2 rounded-xl border text-sm font-bold transition-all whitespace-nowrap ${
          active
            ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 ring-2 ring-blue-500/10'
            : 'border-gray-200 dark:border-dark-border bg-white dark:bg-dark-surface text-gray-600 dark:text-dark-muted hover:border-blue-200'
        }`}
      >
        {facet.icon ? (
          renderFacetIcon(facet.icon, 'w-4 h-4')
        ) : (
          <SlidersHorizontal className="w-4 h-4" />
        )}
        <span>{label}</span>
      </button>
    );
  }

  // --- Single / multi: trigger button + popover --------------------------
  const selected = resolvedValue.kind === 'multi' ? resolvedValue.values : resolvedValue.kind === 'single' ? (resolvedValue.value ? [resolvedValue.value] : []) : [];
  const triggerText = optionLabel(facet, resolvedValue, options);

  return (
    <div className="flex items-center gap-2 min-w-0">
      <div className="relative">
        <button
          ref={triggerRef}
          type="button"
          onClick={() => setIsOpen((o) => !o)}
          className={`flex items-center justify-between gap-2 px-3 py-2 rounded-xl border text-sm font-bold transition-all whitespace-nowrap ${
            active
              ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 ring-2 ring-blue-500/10'
              : 'border-gray-200 dark:border-dark-border bg-white dark:bg-dark-surface text-gray-700 dark:text-dark-text hover:border-blue-200'
          }`}
        >
          <span className="flex items-center gap-2">
            {facet.icon ? (
              renderFacetIcon(facet.icon, `w-4 h-4 ${active ? 'text-blue-500' : 'text-gray-400'}`)
            ) : (
              <Grid className={`w-4 h-4 ${active ? 'text-blue-500' : 'text-gray-400'}`} />
            )}
            <span>{triggerText ?? label}</span>
          </span>
          <ChevronDown className={`w-4 h-4 text-gray-400 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
        </button>

        <Popover
          isOpen={isOpen}
          onClose={() => setIsOpen(false)}
          triggerRef={triggerRef}
          side="bottom"
          align="start"
          sideOffset={8}
        >
          <div className="w-64 bg-white dark:bg-dark-surface rounded-2xl border border-gray-100 dark:border-dark-border shadow-xl overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200 max-h-[300px] overflow-y-auto custom-scrollbar">
            {options.length === 0 ? (
              <div className="px-4 py-3 text-sm text-gray-400 dark:text-dark-muted">No options</div>
            ) : (
              options.map((opt) => {
                const isSelected = selected.includes(opt.value);
                return (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => onToggleOption(opt.value)}
                    className={`w-full flex items-center justify-between px-4 py-3 text-sm font-bold transition-colors ${
                      isSelected
                        ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600'
                        : 'text-gray-600 dark:text-dark-muted hover:bg-gray-50 dark:hover:bg-dark-bg'
                    }`}
                  >
                    <span className="flex items-center gap-3">
                      {opt.icon ? (
                        renderFacetIcon(opt.icon, 'w-4 h-4')
                      ) : (
                        <span className="w-4 h-4 inline-block" />
                      )}
                      <span>{opt.label}</span>
                    </span>
                    <span className="flex items-center gap-2">
                      {opt.count !== undefined && (
                        <span
                          className={`text-[10px] px-2 py-0.5 rounded-full ${
                            isSelected
                              ? 'bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400'
                              : 'bg-gray-100 dark:bg-dark-bg text-gray-400'
                          }`}
                        >
                          {opt.count}
                        </span>
                      )}
                      {isSelected && <CheckCircle2 className="w-4 h-4 text-blue-500" />}
                    </span>
                  </button>
                );
              })
            )}
          </div>
        </Popover>
      </div>

      {/* Active value pills (desktop) */}
      {showActivePills && selected.length > 0 && (
        <div className="hidden lg:flex flex-wrap items-center gap-2 overflow-hidden max-w-[400px]">
          {selected.map((sel) => {
            const opt = options.find((o) => o.value === sel);
            if (!opt) return null;
            return (
              <span
                key={sel}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border border-blue-100 dark:border-blue-800 rounded-lg whitespace-nowrap"
              >
                {opt.icon && renderFacetIcon(opt.icon, 'w-3 h-3')}
                <span>{opt.label}</span>
                <button
                  type="button"
                  onClick={() => onToggleOption(sel)}
                  className="hover:bg-blue-100 dark:hover:bg-blue-800 rounded-full p-0.5 transition-colors"
                  aria-label={`Remove ${opt.label}`}
                >
                  <X className="w-3 h-3" />
                </button>
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
};
