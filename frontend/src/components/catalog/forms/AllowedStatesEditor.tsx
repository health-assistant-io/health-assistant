/**
 * Allowed-state editor for a STATE biomarker (plan state-biomarkers-2026-08-05).
 *
 * A **pure draft editor** — it reads/writes an `AllowedStateSpec[]` array
 * via `onChange` and makes no API calls itself. The catalog form's save
 * flow persists the draft as part of the biomarker create/update payload.
 *
 * Each row lets the admin pick a state from the universal catalog
 * (Positive/Negative/Detected/...) and mark whether it belongs to the
 * "normal set" (the categorical equivalent of a numeric reference range —
 * the analytics status is "Normal" when the observation value is in the
 * normal set, "Abnormal" otherwise).
 *
 * The "add state" control uses the existing SearchableDropdown component
 * with grouped options (by BiomarkerState.category) for easier navigation
 * of the 22-state catalog.
 */
import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Trash2 } from 'lucide-react';
import type { AllowedStateSpec, BiomarkerState } from '../../../types/biomarker';
import { SearchableDropdown, type DropdownOption } from '../../ui/SearchableDropdown';

interface Props {
  /** Currently-selected states (slug-keyed). */
  values: AllowedStateSpec[];
  /** The full state catalog (from biomarkerService.getStates). */
  states: BiomarkerState[];
  onChange: (next: AllowedStateSpec[]) => void;
}

export const AllowedStatesEditor: React.FC<Props> = ({ values, states, onChange }) => {
  const { t } = useTranslation();
  const usedSlugs = useMemo(() => new Set(values.map((v) => v.state_slug)), [values]);
  const available = useMemo(
    () => states.filter((s) => !usedSlugs.has(s.slug)),
    [states, usedSlugs],
  );

  const updateRow = (slug: string, patch: Partial<AllowedStateSpec>) => {
    onChange(values.map((v) => (v.state_slug === slug ? { ...v, ...patch } : v)));
  };
  const removeRow = (slug: string) => onChange(values.filter((v) => v.state_slug !== slug));

  const addRow = (slug: string) => {
    if (!slug) return;
    const state = states.find((s) => s.slug === slug);
    if (!state) return;
    onChange([
      ...values,
      {
        state_slug: state.slug,
        is_normal: false,
        sort_order: values.length,
      },
    ]);
  };

  // Build grouped dropdown options for the SearchableDropdown.
  const dropdownOptions: DropdownOption[] = useMemo(
    () =>
      available.map((s) => ({
        value: s.slug,
        label: s.display,
        description: s.code,
        group: s.category || undefined,
      })),
    [available],
  );

  return (
    <div className="space-y-2">
      {values.length === 0 && (
        <p className="text-xs text-amber-600 dark:text-amber-400">
          {t('biomarker_catalog.allowed_state_empty', 'No states selected — pick at least one.')}
        </p>
      )}

      {values.map((row) => {
        const state = states.find((s) => s.slug === row.state_slug);
        return (
          <div
            key={row.state_slug}
            className="flex items-center gap-2 p-2 rounded-md border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40"
          >
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
                {state?.display ?? row.state_slug}
              </div>
              <div className="text-[11px] text-gray-500 dark:text-gray-400 truncate">
                <code className="font-mono">{state?.code ?? '—'}</code>
                {state?.category && (
                  <>
                    <span className="mx-1">·</span>
                    <span className="truncate">{state.category}</span>
                  </>
                )}
              </div>
            </div>
            <label className="flex items-center gap-1.5 text-xs text-gray-700 dark:text-gray-300 cursor-pointer">
              <input
                type="checkbox"
                checked={!!row.is_normal}
                onChange={(e) => updateRow(row.state_slug, { is_normal: e.target.checked })}
                className="w-3.5 h-3.5 text-emerald-600 rounded border-gray-300 focus:ring-emerald-500"
              />
              {t('biomarker_catalog.allowed_state_normal', 'Normal')}
            </label>
            <button
              type="button"
              onClick={() => removeRow(row.state_slug)}
              className="p-1 text-gray-400 hover:text-red-500 transition-colors"
              aria-label={t('biomarker_catalog.allowed_state_remove', 'Remove')}
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        );
      })}

      {dropdownOptions.length > 0 && (
        <SearchableDropdown
          options={dropdownOptions}
          value=""
          onChange={(slug) => addRow(slug)}
          placeholder={t('biomarker_catalog.allowed_state_add', 'Add state') + '…'}
          searchPlaceholder={t('biomarker_catalog.allowed_state_search', 'Search states…')}
          label={t('biomarker_catalog.allowed_state_add', 'Add state')}
        />
      )}
    </div>
  );
};
