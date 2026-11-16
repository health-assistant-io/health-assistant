import React, { useState, useEffect, useRef } from 'react';
import { Activity, Info, Calendar, Stethoscope } from 'lucide-react';
import { AIAssistButton } from '../ui/AIAssistButton';
import { UnitSelector } from '../ui/UnitSelector';
import { DatePicker } from '../ui/DatePicker';
import { TimePicker } from '../ui/TimePicker';
import { CatalogItemPicker } from '../catalog/CatalogItemPicker';
import biomarkerService from '../../services/biomarkerService';
import { Biomarker, Unit } from '../../types/biomarker';
import { Observation } from '../../types/observation';
import { RecordFormFooter } from '../records/RecordFormFooter';
import { useTranslation } from 'react-i18next';
import { matchBiomarker } from '../../utils/searchUtils';
import type { CatalogSelection } from '../../types/catalog';

/**
 * Prefill shape. Keys mirror the backend `propose_record_biomarker_result`
 * proposed_payload 1:1 (the HITL contract). (task_type is still
 * `add_biomarker_to_examination` — kept stable for SDK alignment.)
 */
export interface AddBiomarkerFormPrefill {
  biomarker_id?: string | null;
  biomarker_name?: string;
  biomarker_slug?: string;
  value?: string | number;
  unit?: string;
  note?: string;
  matched?: boolean;
}

/** The form builds the FHIR Observation and hands it to onSubmit; the caller
 *  performs the actual commit (createObservation). Keeps the FHIR mapping in
 *  one place and mirrors the original AddBiomarkerModal behavior exactly. */
export type AddBiomarkerFormPayload = Partial<Observation>;

interface AddBiomarkerFormProps {
  patientId: string;
  /** Optional — when provided the new observation is linked to this exam.
   *  When omitted the form enters "standalone" mode: a measurement date
   *  field is shown and the resulting observation has no exam link. */
  examinationId?: string;
  /** Lock the form to a single biomarker definition (used by the
   *  BiomarkerDetail "Log Reading" action — the search step is skipped
   *  and the form opens with the biomarker already resolved). */
  lockedBiomarker?: Biomarker;
  prefill?: AddBiomarkerFormPrefill;
  onSubmit: (observation: AddBiomarkerFormPayload) => Promise<void>;
  onCancel?: () => void;
  onReject?: () => void;
  submitLabel?: string;
  rejectLabel?: string;
  /** Owned by the shell; set as the native `<form id>` so a footer button
   *  outside the form (the popup) can submit it through the `form` attribute. */
  formId?: string;
  /** Inline footer for the HITL chat-card placement; the popup shell renders
   *  its own `RecordFormFooter` instead. */
  showActions?: boolean;
  /** Reports validity/loading to the shell so its footer can drive Save. */
  onStateChange?: (state: { canSubmit: boolean; loading: boolean }) => void;
  /**
   * One-shot programmatic pick (HITL confirm-after-create): set the picker to
   * this biomarker and run the full definition hydration (`handlePickerChange`
   * → `getBiomarkerById`) so `selectedBiomarker` is set and Save is enabled.
   * Applies when `catalog_id` CHANGES and the picker does not already hold
   * it; never re-applies after the user clears the chip; a remount with the
   * same value is a no-op (the ref seeds from the prop).
   */
  catalogSelection?: { catalog_id: string; text: string };
}

export const AddBiomarkerForm: React.FC<AddBiomarkerFormProps> = ({
  patientId,
  examinationId,
  lockedBiomarker,
  prefill,
  onSubmit,
  onCancel,
  onReject,
  submitLabel,
  rejectLabel,
  formId,
  showActions = true,
  onStateChange,
  catalogSelection,
}) => {
  const { t } = useTranslation();
  const [selectedBiomarker, setSelectedBiomarker] = useState<Biomarker | null>(null);
  // CatalogItemPicker is controlled on `CatalogSelection[]` (a lightweight
  // {type,id,label} ref). The form needs the full Biomarker object to read
  // value_type / allowed_states / preferred_unit_symbol — bridged via
  // biomarkerService.getBiomarkerById on each pick.
  const [pickerValue, setPickerValue] = useState<CatalogSelection[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  const [loading, setLoading] = useState(false);

  // Standalone-mode extras (hidden when examinationId is supplied). The
  // measurement date defaults to "now" so a saved reading sorts to the top
  // of the trend chart; users can back-date for historical paper records.
  // The date + time are stored separately (matching the project's picker
  // API: DatePicker takes YYYY-MM-DD, TimePicker takes HH:MM 24h), then
  // combined into a single UTC ISO string at submit time.
  const isStandalone = !examinationId;
  const splitLocalNow = () => {
    const d = new Date();
    d.setSeconds(0, 0);
    const pad = (n: number) => String(n).padStart(2, '0');
    return {
      measuredDate: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
      measuredTime: `${pad(d.getHours())}:${pad(d.getMinutes())}`,
    };
  };
  const initialNow = splitLocalNow();

  const [formData, setFormData] = useState({
    value: '',
    unit: '',
    note: '',
    measuredDate: initialNow.measuredDate,
    measuredTime: initialNow.measuredTime,
    method: '',
  });

  // Load units + hydrate from prefill (HITL proposal) on mount.
  useEffect(() => {
    biomarkerService.getUnits().then(setUnits);

    // Lock mode: caller already knows the biomarker. Skip the catalog
    // search step entirely — the user just fills in the value.
    if (lockedBiomarker) {
      setSelectedBiomarker(lockedBiomarker);
      setFormData(prev => ({
        ...prev,
        unit: lockedBiomarker.preferred_unit_symbol || '',
      }));
      return;
    }

    if (prefill) {
      setFormData(prev => ({
        ...prev,
        value: prefill.value != null && prefill.value !== '' ? String(prefill.value) : '',
        unit: prefill.unit || '',
        note: prefill.note || '',
      }));

      const idOrName = prefill.biomarker_id || prefill.biomarker_name;
      if (idOrName) {
        (async () => {
          try {
            const all = await biomarkerService.getAllBiomarkers();
            const match = prefill.biomarker_id
              ? all.find(b => b.id === prefill.biomarker_id)
              : all.find(b => matchBiomarker(b, prefill.biomarker_name!));
            if (match) {
              setSelectedBiomarker(match);
              setPickerValue([{ type: 'biomarker', id: match.id, label: match.name }]);
              setFormData(prev => ({ ...prev, unit: prev.unit || match.preferred_unit_symbol || '' }));
            }
          } catch (err) {
            console.error('Failed to resolve prefilled biomarker', err);
          }
        })();
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Bridge CatalogItemPicker → the full Biomarker definition the form needs. */
  const handlePickerChange = async (next: CatalogSelection[]) => {
    setPickerValue(next);
    if (next.length === 0) {
      setSelectedBiomarker(null);
      return;
    }
    const picked = next[0];
    try {
      const bio = await biomarkerService.getBiomarkerById(picked.id);
      setSelectedBiomarker(bio);
      setFormData(prev => ({ ...prev, unit: bio.preferred_unit_symbol || prev.unit }));
    } catch (err) {
      console.error('Failed to fetch picked biomarker definition', err);
      setSelectedBiomarker(null);
    }
  };

  // One-shot programmatic pick (HITL confirm-after-create). Delegates to the
  // same hydration bridge as a manual pick so `selectedBiomarker` is set and
  // Save is enabled. The ref seeds from the prop so a remount with the same
  // value is a no-op, and the value never re-applies once the user clears.
  const appliedCatalogRef = useRef<string | null>(catalogSelection?.catalog_id ?? null);
  useEffect(() => {
    const id = catalogSelection?.catalog_id;
    if (!id || appliedCatalogRef.current === id) return;
    appliedCatalogRef.current = id;
    if (pickerValue.some((s) => s.id === id)) return;
    void handlePickerChange([
      { type: 'biomarker', id, label: catalogSelection?.text ?? '' },
    ]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalogSelection]);

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!selectedBiomarker || !formData.value) return;

    setLoading(true);
    try {
      // Construct FHIR Observation. STATE biomarkers build a
      // valueCodeableConcept with the picked state code; QUANTITY biomarkers
      // build a valueQuantity as before. The backend's hard validator
      // (Observation↔BiomarkerDefinition contract) enforces the right shape.
      const isStateBiomarker = selectedBiomarker.value_type === 'state';
      const observation: any = {
        patient_id: patientId,
        biomarker_id: selectedBiomarker.id,
        status: 'final',
        category: [{
          coding: [{
            system: 'http://terminology.hl7.org/CodeSystem/observation-category',
            code: 'laboratory',
            display: 'Laboratory'
          }]
        }],
        code: {
          coding: [{
            system: selectedBiomarker.coding_system === 'custom' ? 'urn:uuid:health-assistant:custom-biomarker' : selectedBiomarker.coding_system === 'snomed' ? 'http://snomed.info/sct' : 'http://loinc.org',
            code: selectedBiomarker.code || selectedBiomarker.slug,
            display: selectedBiomarker.name
          }],
          text: selectedBiomarker.name
        },
        // No ``interpretation`` field: status is always recomputed from
        // value + reference range (frontend getFinalStatus) or from
        // allowed_states.is_normal (STATE biomarkers), so a user-supplied
        // Low/Normal/High toggle was a no-op for ranged biomarkers and
        // meaningless for STATE biomarkers. See useBiomarkers + analytics
        // service for the canonical status pipeline.
        note: formData.note ? [{ text: formData.note }] : []
      };

      // Link to the exam when supplied, otherwise stamp the standalone
      // measurement date + optional method so the reading sorts correctly
      // in the longitudinal trend and shows a "manual" provenance chip.
      if (examinationId) {
        observation.examination_id = examinationId;
      } else if (formData.measuredDate) {
        // Combine the picker outputs (YYYY-MM-DD + HH:MM) into a UTC ISO
        // timestamp. Treat the picked wall-clock time as the user's local
        // timezone (the same semantics as a datetime-local input).
        const combined = new Date(`${formData.measuredDate}T${formData.measuredTime || '00:00'}`);
        observation.effective_datetime = combined.toISOString();
      }
      if (formData.method && !examinationId) {
        observation.method = formData.method.trim();
      }

      if (isStateBiomarker) {
        // Resolve the picked state slug → code + system from the
        // biomarker's allowed_states catalog.
        const pickedState = (selectedBiomarker.allowed_states ?? []).find(
          (s) => s.state_slug === formData.value || s.code === formData.value || s.display === formData.value,
        );
        observation.value_codeable_concept = {
          coding: [{
            code: pickedState?.code ?? formData.value,
            system: pickedState?.system ?? 'http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation',
            display: pickedState?.display ?? formData.value,
          }],
        };
      } else {
        observation.value_quantity = {
          value: parseFloat(formData.value),
          unit: formData.unit || selectedBiomarker.preferred_unit_symbol,
          system: 'http://unitsofmeasure.org',
          code: formData.unit || selectedBiomarker.preferred_unit_symbol
        };
      }

      await onSubmit(observation as unknown as AddBiomarkerFormPayload);
    } finally {
      setLoading(false);
    }
  };

  const canSubmit = !loading && Boolean(selectedBiomarker) && Boolean(formData.value);

  const reportedState = useRef('');
  useEffect(() => {
    const key = `${canSubmit}|${loading}`;
    if (reportedState.current === key) return;
    reportedState.current = key;
    onStateChange?.({ canSubmit, loading });
  }, [canSubmit, loading, onStateChange]);

  return (
    <>
      <form id={formId} onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col">
        <div className="flex-1 min-h-0 overflow-y-auto p-8 space-y-8">
          <div className="flex items-center justify-end">
            <AIAssistButton
              taskType="fill_biomarker_form"
              context={{ patientId, examinationId: examinationId || undefined }}
              onSuggestedData={async (data) => {
                setFormData(prev => ({
                  ...prev,
                  value: (data.value !== undefined && data.value !== null) ? data.value.toString() : prev.value,
                  unit: data.unit || prev.unit,
                  note: data.note || prev.note
                }));

                if (data.biomarker_name && !selectedBiomarker) {
                  try {
                    const all = await biomarkerService.getAllBiomarkers();
                    const match = all.find(b => matchBiomarker(b, data.biomarker_name));

                    if (match) {
                      setSelectedBiomarker(match);
                      setPickerValue([{ type: 'biomarker', id: match.id, label: match.name }]);
                      if (!data.unit) {
                        setFormData(prev => ({ ...prev, unit: match.preferred_unit_symbol || prev.unit }));
                      }
                    }
                  } catch (err) {
                    console.error('Failed to auto-select biomarker', err);
                  }
                }
              }}
            />
          </div>
        {/* Locked-biomarker badge — surfaces the target biomarker so the user
            has clear context (otherwise the whole catalog-search block, which
            contains the name, is hidden in lock mode). */}
        {lockedBiomarker && (
          <div className="p-4 bg-blue-50 dark:bg-blue-900/10 border border-blue-100 dark:border-blue-900/30 rounded-2xl">
            <div className="flex items-center space-x-3">
              <div className="p-2 bg-blue-600 text-white rounded-lg">
                <Activity className="w-4 h-4" />
              </div>
              <div className="min-w-0">
                <h4 className="font-bold text-blue-900 dark:text-blue-300 truncate">{lockedBiomarker.name}</h4>
                <p className="text-[10px] text-blue-600 dark:text-blue-400 uppercase font-black tracking-widest">
                  {lockedBiomarker.slug}
                  {lockedBiomarker.reference_range_min != null && lockedBiomarker.reference_range_max != null
                    ? ` · ${lockedBiomarker.reference_range_min}–${lockedBiomarker.reference_range_max} ${lockedBiomarker.preferred_unit_symbol || ''}`
                    : ''}
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Biomarker Selection — hidden when the caller locks the biomarker
            (BiomarkerDetail "Log Reading" flow skips the catalog search).
            Uses the project's CatalogItemPicker so the user gets the same
            search + Browse-modal experience as the rest of the catalog UIs;
            definitions are created from the browse dialog (allowCreate). */}
        {!lockedBiomarker && (
          <div className="space-y-3">
            <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">
              {t('examination_detail.add_biomarker.search_catalog')}
            </label>
            <CatalogItemPicker
              mode="single"
              allowedTypes={['biomarker']}
              value={pickerValue}
              onChange={handlePickerChange}
              placeholder={t('examination_detail.add_biomarker.search_placeholder')}
              displayMode="cards"
              block
              allowCreate
            />
          </div>
        )}

        {/* Standalone-only fields: measurement date + optional method.
            Hidden inside an examination (the exam's own date wins).
            Uses the project's DatePicker + TimePicker components — the same
            idiom as ClinicalEventForm. Hidden inside an examination. */}
        {isStandalone && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="space-y-3">
              <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1 flex items-center">
                <Calendar className="w-3 h-3 mr-2" />
                {t('biomarkers.log_reading.measured_at', 'Measurement Date')}
              </label>
              <DatePicker
                placeholder={t('common.select_date', 'Select date')}
                value={formData.measuredDate}
                onChange={(date) => setFormData(prev => ({ ...prev, measuredDate: date }))}
                required
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 font-bold"
              />
              <TimePicker
                value={formData.measuredTime}
                onChange={(time) => setFormData(prev => ({ ...prev, measuredTime: time }))}
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 font-bold"
              />
            </div>
            <div className="space-y-3">
              <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1 flex items-center">
                <Stethoscope className="w-3 h-3 mr-2" />
                {t('biomarkers.log_reading.method', 'Method (optional)')}
              </label>
              <input
                type="text"
                placeholder={t('biomarkers.log_reading.method_placeholder', 'e.g. Fingerstick, Home BP cuff, Lab draw')}
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 font-medium"
                value={formData.method}
                onChange={(e) => setFormData({ ...formData, method: e.target.value })}
              />
            </div>
          </div>
        )}

        {/* Form Fields */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="space-y-3">
            <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">
              {selectedBiomarker?.value_type === 'state'
                ? t('biomarkers.state_value', 'State')
                : t('examination_detail.add_biomarker.value')}
            </label>
            {selectedBiomarker?.value_type === 'state' ? (
              <select
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 font-bold"
                value={formData.value}
                onChange={(e) => setFormData({ ...formData, value: e.target.value })}
                required
              >
                <option value="">{t('biomarkers.state_value_placeholder', 'Select a state…')}</option>
                {(selectedBiomarker.allowed_states ?? []).map((s) => (
                  <option key={s.state_slug} value={s.state_slug}>
                    {s.display} ({s.code})
                  </option>
                ))}
              </select>
            ) : (
              <input
                type="number"
                step="any"
                placeholder="0.00"
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 font-bold"
                value={formData.value}
                onChange={e => setFormData({...formData, value: e.target.value})}
                required
              />
            )}
          </div>

          <div className="space-y-3">
            <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">{t('examination_detail.add_biomarker.unit')}</label>
            {selectedBiomarker?.value_type === 'state' ? (
              <p className="px-4 py-3 text-sm text-gray-400 dark:text-dark-muted italic">
                {t('biomarker_catalog.state_no_unit', 'State biomarkers carry no unit (categorical values are unitless).')}
              </p>
            ) : (
              <UnitSelector
                units={units}
                selectedSymbol={formData.unit}
                onSelect={(u) => setFormData(prev => ({ ...prev, unit: u.symbol }))}
                onUnitsUpdated={setUnits}
                placeholder={t('examination_detail.add_biomarker.select_unit')}
              />
            )}
          </div>

          <div className="col-span-1 md:col-span-2 space-y-3">
            <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1 flex items-center">
              <Info className="w-3 h-3 mr-2" />
              {t('examination_detail.add_biomarker.observations')}
            </label>
            <textarea
              rows={3}
              className="w-full px-4 py-4 bg-gray-50 dark:bg-dark-bg border-none rounded-2xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 resize-none text-sm"
              placeholder={t('examination_detail.add_biomarker.observations_placeholder')}
              value={formData.note}
              onChange={e => setFormData({...formData, note: e.target.value})}
            />
          </div>
        </div>
        </div>

        {showActions && (
          <div className="flex shrink-0 items-center border-t border-gray-50 bg-gray-50 px-8 py-6 dark:border-dark-border dark:bg-dark-bg/50">
            <RecordFormFooter
              formId={formId}
              canSubmit={canSubmit}
              loading={loading}
              submitLabel={
                submitLabel || t('examination_detail.add_biomarker.add_result')
              }
              cancelLabel={t('common.cancel')}
              onCancel={onCancel}
              rejectLabel={rejectLabel}
              onReject={onReject}
            />
          </div>
        )}
      </form>
    </>
  );
};
