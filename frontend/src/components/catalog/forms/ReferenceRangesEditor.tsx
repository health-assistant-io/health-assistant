/**
 * Stratified reference-range editor for a biomarker (audit B9/F3).
 *
 * A **pure draft editor** — it reads/writes a `ranges` array via `onChange`
 * and makes no API calls itself. This lets it work identically in create and
 * edit mode (the catalog form's save flow reconciles the draft against the
 * server via `biomarkerService.syncReferenceRanges` once the biomarker has an
 * id). Each row is a labelled card: Sex / Age window / Range / Unit, with a
 * remove control.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, Trash2 } from 'lucide-react';
import type { BiomarkerReferenceRange, Unit } from '../../../types/biomarker';

type Sex = 'MALE' | 'FEMALE' | 'OTHER' | 'UNKNOWN';

const SEX_OPTIONS: { value: '' | Sex; label: string }[] = [
  { value: '', label: 'Any sex' },
  { value: 'MALE', label: 'Male' },
  { value: 'FEMALE', label: 'Female' },
  { value: 'OTHER', label: 'Other' },
];

const selectCls =
  'w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-2 py-1.5 text-sm focus:ring-2 focus:ring-blue-500 outline-none';

const inputCls =
  'w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-2 py-1.5 text-sm focus:ring-2 focus:ring-blue-500 outline-none';

const Label: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <span className="block text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-1">
    {children}
  </span>
);

/** A controlled number input that keeps ``''`` for empty (stores null). */
const NumberInput: React.FC<{
  value: number | null | undefined;
  onChange: (v: number | null) => void;
  placeholder?: string;
}> = ({ value, onChange, placeholder }) => {
  const text = value == null ? '' : String(value);
  return (
    <input
      type="number"
      inputMode="decimal"
      className={inputCls}
      value={text}
      placeholder={placeholder ?? '—'}
      onChange={(e) => {
        const t = e.target.value;
        onChange(t === '' ? null : Number(t));
      }}
    />
  );
};

export const ReferenceRangesEditor: React.FC<{
  ranges: BiomarkerReferenceRange[];
  onChange: (ranges: BiomarkerReferenceRange[]) => void;
  units: Unit[];
}> = ({ ranges, onChange, units }) => {
  const { t } = useTranslation();

  const update = (index: number, patch: Partial<BiomarkerReferenceRange>) => {
    const next = ranges.slice();
    next[index] = { ...next[index], ...patch };
    onChange(next);
  };

  const addRange = () => {
    onChange([
      ...ranges,
      { sex: null, age_min: null, age_max: null, low: null, high: null, unit_id: null },
    ]);
  };

  const removeRange = (index: number) => {
    onChange(ranges.filter((_, i) => i !== index));
  };

  if (ranges.length === 0) {
    return (
      <div className="space-y-2">
        <p className="text-[11px] text-gray-400">
          {t(
            'biomarker_catalog.ranges_empty',
            'No stratified ranges — the default range above applies to everyone. Add demographic-specific ranges (e.g. separate male/female or pediatric) below.',
          )}
        </p>
        <button
          type="button"
          onClick={addRange}
          className="flex items-center gap-1.5 text-xs font-medium text-blue-600 dark:text-blue-400 hover:underline"
        >
          <Plus className="w-3.5 h-3.5" />
          {t('biomarker_catalog.add_range', 'Add stratified range')}
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {ranges.map((r, i) => (
        <div
          key={(r.id ?? `new-${i}`)}
          className="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40 p-3"
        >
          <div className="flex items-start gap-3">
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-3 gap-y-2 flex-1">
              <div>
                <Label>Sex</Label>
                <select
                  className={selectCls}
                  value={r.sex ?? ''}
                  onChange={(e) =>
                    update(i, { sex: (e.target.value || null) as BiomarkerReferenceRange['sex'] })
                  }
                >
                  {SEX_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <Label>Age min (yrs)</Label>
                <NumberInput value={r.age_min} onChange={(v) => update(i, { age_min: v })} />
              </div>
              <div>
                <Label>Age max (yrs)</Label>
                <NumberInput value={r.age_max} onChange={(v) => update(i, { age_max: v })} />
              </div>
              <div>
                <Label>Low</Label>
                <NumberInput value={r.low} onChange={(v) => update(i, { low: v })} />
              </div>
              <div>
                <Label>High</Label>
                <NumberInput value={r.high} onChange={(v) => update(i, { high: v })} />
              </div>
              <div>
                <Label>Unit</Label>
                <select
                  className={selectCls}
                  value={r.unit_id ?? ''}
                  onChange={(e) => update(i, { unit_id: e.target.value || null })}
                >
                  <option value="">any unit</option>
                  {units.map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.symbol}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <button
              type="button"
              onClick={() => removeRange(i)}
              className="mt-5 flex items-center justify-center p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-md transition-colors"
              title={t('common.delete', 'Remove')}
              aria-label={t('common.delete', 'Remove range')}
            >
              <Trash2 className="w-4 h-4" />
            </button>
          </div>
          {r.text ? (
            <p className="mt-1 text-[10px] text-gray-400">{r.text}</p>
          ) : null}
        </div>
      ))}
      <button
        type="button"
        onClick={addRange}
        className="flex items-center gap-1.5 text-xs font-medium text-blue-600 dark:text-blue-400 hover:underline"
      >
        <Plus className="w-3.5 h-3.5" />
        {t('biomarker_catalog.add_range', 'Add another range')}
      </button>
    </div>
  );
};
