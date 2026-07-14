/**
 * Vaccine create/edit form — slug, name, code (CVX), target diseases, dose
 * schedule, contraindications, side effects. Mirrors `VaccineCatalogCreate`.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Syringe } from 'lucide-react';
import type { CatalogItemFormProps } from './catalogForms';
import { Field, TextInput } from './FormFields';
import { RichTextField } from './RichTextField';
import { RepeatableItems } from '../../ui/RepeatableItems';

interface DoseSchedule {
  doses: number | null;
  intervals: string[];
}

function readSchedule(values: Record<string, unknown>): DoseSchedule {
  const raw = values.dose_schedule;
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    const obj = raw as Record<string, unknown>;
    const doses =
      typeof obj.doses === 'number'
        ? obj.doses
        : typeof obj.doses === 'string'
        ? Number(obj.doses)
        : null;
    const intervals = Array.isArray(obj.intervals)
      ? (obj.intervals.filter((s) => typeof s === 'string') as string[])
      : [];
    return {
      doses: Number.isFinite(doses as number) ? (doses as number) : null,
      intervals,
    };
  }
  return { doses: null, intervals: [] };
}

export const VaccineForm: React.FC<CatalogItemFormProps> = ({
  values,
  onChange,
}) => {
  const { t } = useTranslation();
  const targetDiseases = Array.isArray(values.target_diseases)
    ? (values.target_diseases as string[])
    : [];
  const sideEffects = Array.isArray(values.side_effects)
    ? (values.side_effects as string[])
    : [];

  const schedule = readSchedule(values as unknown as Record<string, unknown>);
  const intervals = schedule.intervals ?? [];

  const patchSchedule = (next: Partial<DoseSchedule>) => {
    const merged: DoseSchedule = {
      doses: schedule.doses ?? null,
      intervals: schedule.intervals ?? [],
      ...next,
    };
    // Drop empty schedules entirely so we don't persist `{doses: null, intervals: []}`.
    if ((merged.doses == null || merged.doses === 0) && merged.intervals.length === 0) {
      onChange({ dose_schedule: null });
    } else {
      onChange({
        dose_schedule: {
          doses: merged.doses ?? null,
          intervals: merged.intervals,
        },
      });
    }
  };

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <Field label={t('catalogs.field_name', 'Name')}>
          <TextInput
            value={String(values.name ?? '')}
            onChange={(e) => onChange({ name: e.target.value })}
          />
        </Field>
        <Field label="Slug" hint={t('catalogs.field_slug_hint', 'Unique key (no spaces)')}>
          <TextInput
            value={String(values.slug ?? '')}
            onChange={(e) => onChange({ slug: e.target.value })}
            placeholder="e.g. mmr"
          />
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="CVX code">
          <TextInput
            value={String(values.code ?? '')}
            onChange={(e) => onChange({ code: e.target.value })}
            placeholder="e.g. 03"
          />
        </Field>
        <Field label={t('catalogs.field_coding_system', 'Coding system')}>
          <TextInput
            value={String(values.coding_system ?? 'cvx')}
            onChange={(e) => onChange({ coding_system: e.target.value })}
          />
        </Field>
      </div>
      <RichTextField
        label={t('catalogs.field_description', 'Description')}
        value={String(values.description ?? '')}
        onChange={(html) => onChange({ description: html })}
      />
      <Field
        label={t('catalogs.field_target_diseases', 'Target diseases')}
        hint={t('catalogs.field_aliases_hint', 'Comma-separated')}
      >
        <TextInput
          value={targetDiseases.join(', ')}
          onChange={(e) =>
            onChange({
              target_diseases: e.target.value
                .split(',')
                .map((s) => s.trim())
                .filter(Boolean),
            })
          }
          placeholder="Measles, Mumps, Rubella"
        />
      </Field>

      {/* Dose Schedule */}
      <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-900/40 p-3 space-y-3">
        <div className="flex items-center gap-2">
          <Syringe className="w-4 h-4 text-blue-500" />
          <p className="text-xs font-semibold text-gray-700 dark:text-gray-200">
            {t('catalogs.field_dose_schedule', 'Dose Schedule')}
          </p>
        </div>
        <Field label={t('catalogs.field_dose_count', 'Number of doses')}>
          <TextInput
            type="number"
            min={0}
            value={schedule.doses == null ? '' : String(schedule.doses)}
            onChange={(e) => {
              const v = e.target.value;
              patchSchedule({ doses: v === '' ? null : Math.max(0, parseInt(v, 10) || 0) });
            }}
            placeholder={t('catalogs.field_dose_count_placeholder', 'e.g. 2')}
          />
        </Field>
        <RepeatableItems<string>
          title={t('catalogs.field_dose_intervals', 'Intervals between doses')}
          hint={t('catalogs.field_dose_intervals_hint', 'e.g. "0, 1, 6 months"')}
          addItemLabel={t('catalogs.field_dose_interval_add', 'Add interval')}
          items={intervals}
          onChange={(next) => patchSchedule({ intervals: next })}
          createItem={() => ''}
          emptyMessage={t('catalogs.field_dose_intervals_empty', 'No intervals recorded yet.')}
          renderItem={(val, patch) => (
            <TextInput
              value={val}
              onChange={(e) => patch(e.target.value)}
              placeholder={t('catalogs.field_dose_interval_placeholder', 'e.g. 1 month')}
            />
          )}
        />
      </div>

      <RichTextField
        label={t('catalogs.field_contraindications', 'Contraindications')}
        value={String(values.contraindications ?? '')}
        onChange={(html) => onChange({ contraindications: html })}
      />
      <Field
        label={t('catalogs.field_side_effects', 'Side effects')}
        hint={t('catalogs.field_aliases_hint', 'Comma-separated')}
      >
        <TextInput
          value={sideEffects.join(', ')}
          onChange={(e) =>
            onChange({
              side_effects: e.target.value
                .split(',')
                .map((s) => s.trim())
                .filter(Boolean),
            })
          }
          placeholder="Fever, Sore arm"
        />
      </Field>
    </div>
  );
};
