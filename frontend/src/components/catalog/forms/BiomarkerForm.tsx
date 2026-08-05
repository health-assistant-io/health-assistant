/**
 * Biomarker create/edit form — slug, code, aliases, reference range, preferred
 * unit, IoT telemetry toggle, and rich-text clinical info. Fields mirror the
 * backend `BiomarkerCreate` schema. Editing now lives here (the detail page is
 * read-only), so this form owns the `is_telemetry` flag whose flip triggers the
 * FHIR↔TimescaleDB data migration on save.
 *
 * Single source of truth for biomarker-catalog creation/editing. Used by:
 *  - The Catalog workspace (`/catalogs?type=biomarker` → New/Edit)
 *  - The HITL `propose_define_biomarker` handler (AI-proposed definitions)
 *  - The legacy `CreateBiomarkerModal` (manual create from the examinations page)
 */
import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Activity } from 'lucide-react';
import type { CatalogItemFormProps } from './catalogForms';
import { Field, TextInput } from './FormFields';
import { ChipInput } from '../../ui/ChipInput';
import { RichTextEditor } from '../../ui/RichTextEditor';
import { ReferenceRangesEditor } from './ReferenceRangesEditor';
import { AllowedStatesEditor } from './AllowedStatesEditor';
import { LinksSection } from '../../ai/hitl/LinksSection';
import biomarkerService from '../../../services/biomarkerService';
import type { Unit, BiomarkerReferenceRange, BiomarkerState, AllowedStateSpec } from '../../../types/biomarker';
import type { CatalogSelection } from '../../../types/catalog';
import { CodingSystemSelect } from '../../ui/CodingSystemSelect';

export const BiomarkerForm: React.FC<CatalogItemFormProps> = ({
  values,
  onChange,
}) => {
  const { t } = useTranslation();
  const [units, setUnits] = useState<Unit[]>([]);
  const [states, setStates] = useState<BiomarkerState[]>([]);

  useEffect(() => {
    biomarkerService.getUnits().then(setUnits).catch(() => {});
    biomarkerService.getStates().then(setStates).catch(() => {});
  }, []);

  const aliases = Array.isArray(values.aliases) ? (values.aliases as string[]) : [];
  const codingSystem = String(values.coding_system ?? 'custom');
  const isTelemetry = Boolean(values.is_telemetry);
  const valueType = String(values.value_type ?? 'quantity') as 'quantity' | 'state';
  const isState = valueType === 'state';
  const allowedStates = useMemo<AllowedStateSpec[]>(
    () => (Array.isArray(values.allowed_states) ? (values.allowed_states as AllowedStateSpec[]) : []),
    [values.allowed_states],
  );

  // Auto-generate slug from name unless the user has manually edited it.
  // Tracks whether the slug field was touched; once touched, we stop
  // auto-filling so manual edits survive.
  const slugTouchedRef = React.useRef(Boolean(values.slug));
  const handleNameChange = (name: string) => {
    if (!slugTouchedRef.current) {
      const generated = name
        .toLowerCase()
        .trim()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '')
        .slice(0, 80) || '';
      // Single onChange so the parent merges both fields atomically —
      // two separate calls capture stale state from the closure.
      onChange({ name, slug: generated });
    } else {
      onChange({ name });
    }
  };
  const handleSlugChange = (slug: string) => {
    slugTouchedRef.current = true;
    onChange({ slug });
  };

  // Switching to STATE: clear the QUANTITY-only fields so the backend's
  // cross-field validator accepts the payload (and the DB CHECK constraint
  // doesn't reject state+unit). Switching back to QUANTITY: drop
  // allowed_states for symmetry.
  const handleValueTypeChange = (next: 'quantity' | 'state') => {
    if (next === valueType) return;
    if (next === 'state') {
      onChange({
        value_type: 'state',
        is_telemetry: false,
        preferred_unit_id: null,
        reference_range_min: null,
        reference_range_max: null,
        reference_ranges: [],
      });
    } else {
      onChange({ value_type: 'quantity', allowed_states: [], supports_multi_state: false });
    }
  };

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <Field label={t('catalogs.field_name', 'Name')}>
          <TextInput
            value={String(values.name ?? '')}
            onChange={(e) => handleNameChange(e.target.value)}
          />
        </Field>
        <Field label="Slug" hint={t('catalogs.field_slug_hint', 'Auto-generated from name — edit to override')}>
          <TextInput
            value={String(values.slug ?? '')}
            onChange={(e) => handleSlugChange(e.target.value)}
            placeholder="auto-generated-from-name"
          />
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Coding system">
          <CodingSystemSelect
            domain="biomarker"
            value={codingSystem}
            onChange={(v) => onChange({ coding_system: v })}
          />
        </Field>
        <Field label="Code">
          <TextInput
            value={String(values.code ?? '')}
            onChange={(e) => onChange({ code: e.target.value })}
            placeholder="e.g. 2345-7"
          />
        </Field>
      </div>
      <Field label="Aliases" hint={t('catalogs.field_aliases_hint', 'Press Enter or comma to add')}>
        <ChipInput
          value={aliases}
          onChange={(next) => onChange({ aliases: next })}
          placeholder={t('catalogs.field_aliases_placeholder', 'e.g. FBS')}
        />
      </Field>

      {/* Value-type discriminator (plan state-biomarkers-2026-08-05).
          Drives which of the value-shape blocks below are visible. */}
      <Field
        label={t('biomarkers.value_type', 'Value type')}
        hint={t(
          'biomarkers.value_type_hint',
          'Quantity = numeric measurements (glucose, heart rate). State = categorical results drawn from a controlled vocabulary (Positive/Negative/Detected/...).',
        )}
      >
        <div className="flex gap-2 p-1 bg-gray-100 dark:bg-gray-800 rounded-lg w-fit">
          {(['quantity', 'state'] as const).map((vt) => (
            <button
              key={vt}
              type="button"
              onClick={() => handleValueTypeChange(vt)}
              className={`px-4 py-1.5 text-sm rounded-md transition-colors ${
                valueType === vt
                  ? 'bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 shadow-sm font-medium'
                  : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'
              }`}
            >
              {vt === 'quantity'
                ? t('biomarkers.value_type_quantity', 'Quantity (numeric)')
                : t('biomarkers.value_type_state', 'State (categorical)')}
            </button>
          ))}
        </div>
      </Field>

      {isState ? (
        <>
          {/* STATE biomarker: the allowed-states picker replaces the
              QUANTITY unit/ref-range grid + the stratified ReferenceRangesEditor.
              The normal-set (is_normal) flag is the categorical equivalent of
              a numeric reference range. */}
          <Field
            label={t('biomarker_catalog.allowed_states', 'Allowed states')}
            hint={t(
              'biomarker_catalog.allowed_states_hint',
              'The states this biomarker accepts. Mark each state that should be considered "normal" — the analytics status will be Normal when the observation value is in the normal set, Abnormal otherwise.',
            )}
          >
            <AllowedStatesEditor
              values={allowedStates}
              states={states}
              onChange={(next) => onChange({ allowed_states: next })}
            />
          </Field>
          <Field
            label={t('biomarkers.multi_state_panel', 'Multi-state panel')}
            hint={t(
              'biomarkers.multi_state_hint',
              'Allow observations with multiple component[] entries (e.g. one organism per row in a microbiology panel).',
            )}
          >
            <label className="flex items-center space-x-2 text-sm text-gray-700 dark:text-gray-300">
              <input
                type="checkbox"
                checked={Boolean(values.supports_multi_state)}
                onChange={(e) => onChange({ supports_multi_state: e.target.checked })}
                className="w-4 h-4 text-indigo-600 rounded border-gray-300 focus:ring-indigo-500"
              />
              <span>{t('biomarkers.multi_state_panel', 'Multi-state panel')}</span>
            </label>
          </Field>
        </>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-3">
            <Field label="Ref. min">
              <TextInput
                type="number"
                value={String(values.reference_range_min ?? '')}
                onChange={(e) =>
                  onChange({
                    reference_range_min:
                      e.target.value === '' ? null : Number(e.target.value),
                  })
                }
              />
            </Field>
            <Field label="Ref. max">
              <TextInput
                type="number"
                value={String(values.reference_range_max ?? '')}
                onChange={(e) =>
                  onChange({
                    reference_range_max:
                      e.target.value === '' ? null : Number(e.target.value),
                  })
                }
              />
            </Field>
            <Field label="Preferred unit">
              <select
                value={String(values.preferred_unit_id ?? '')}
                onChange={(e) => onChange({ preferred_unit_id: e.target.value || null })}
                className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm"
              >
                <option value="">—</option>
                {units.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.symbol} ({u.name})
                  </option>
                ))}
              </select>
            </Field>
          </div>

          {/* Stratified reference ranges (audit B9/F3).
              A pure draft editor — works on create AND edit. The catalog
              workspace reconciles the draft against the server after the
              biomarker is saved (so ranges can be set on first creation too).
              The default range above is the resolver fallback. */}
          <Field
            label={t('biomarker_catalog.reference_ranges', 'Stratified reference ranges')}
            hint={t(
              'biomarker_catalog.reference_ranges_hint',
              'Optional demographic-specific ranges (sex/age/unit). The most specific match wins; otherwise the default range above is used.',
            )}
          >
            <ReferenceRangesEditor
              ranges={(values.reference_ranges as BiomarkerReferenceRange[]) ?? []}
              onChange={(next) => onChange({ reference_ranges: next })}
              units={units}
            />
          </Field>
        </>
      )}

      {/* IoT telemetry toggle — flipping this on save triggers the
          FHIR↔TimescaleDB data migration (see migrate_biomarker_data task).
          The catalog workspace gates the save with a confirmation modal.
          Disabled for STATE biomarkers — telemetry_data.value is Float NOT
          NULL and categorical values have nowhere to go. */}
      <Field label={t('biomarkers.iot_telemetry', 'IoT Telemetry Metric')}>
        <label
          className={`flex items-start space-x-3 p-4 rounded-xl border transition-colors ${
            isState
              ? 'opacity-50 cursor-not-allowed bg-gray-100 dark:bg-gray-900/20 border-gray-200 dark:border-gray-700'
              : 'hover:border-gray-300 dark:hover:border-gray-700 cursor-pointer bg-gray-50 dark:bg-gray-900/40 border-gray-200 dark:border-gray-600'
          }`}
        >
          <input
            type="checkbox"
            checked={isTelemetry}
            disabled={isState}
            onChange={(e) => onChange({ is_telemetry: e.target.checked })}
            className="mt-0.5 w-4 h-4 text-indigo-600 rounded border-gray-300 focus:ring-indigo-500"
          />
          <div className="flex flex-col">
            <span className="text-sm font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-1.5">
              <Activity className="w-3.5 h-3.5 text-indigo-500" />
              {t('biomarkers.iot_telemetry', 'IoT Telemetry Metric')}
            </span>
            <span className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">
              {isState
                ? t(
                    'biomarkers.state_no_telemetry',
                    'State biomarkers cannot be telemetry metrics — categorical values cannot be stored on the numeric telemetry hypertable.',
                  )
                : t(
                    'biomarkers.iot_telemetry_hint',
                    'Routes continuous high-frequency data (heart rate, steps, SpO2, CGM) to TimescaleDB. Changing this migrates all existing historical data between databases — may take a while for large datasets.',
                  )}
            </span>
          </div>
        </label>
      </Field>

      <Field label={t('biomarker_catalog.detailed_info', 'Info / Clinical Significance')}>
        <RichTextEditor
          value={String(values.info ?? '')}
          onChange={(html: string) => onChange({ info: html })}
          placeholder={t(
            'biomarker_catalog.info_placeholder',
            'Describe what this biomarker represents, normal ranges, and clinical significance…',
          )}
        />
      </Field>

      {/* AI-proposed graph links (MEMBER_OF panel / AFFECTS organ / etc.).
          Hidden when the matrix offers no destinations for biomarkers. */}
      <LinksSection
        srcType="biomarker"
        value={Array.isArray(values.links) ? (values.links as CatalogSelection[]) : []}
        onChange={(next) => onChange({ links: next })}
        hideWhenEmpty
      />
    </div>
  );
};
