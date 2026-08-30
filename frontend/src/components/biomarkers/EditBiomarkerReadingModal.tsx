import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import { Save, X, Calendar, Stethoscope, Info, Activity, FlaskConical } from 'lucide-react';
import { DatePicker } from '../ui/DatePicker';
import { TimePicker } from '../ui/TimePicker';
import { UnitSelector } from '../ui/UnitSelector';
import { updateObservation } from '../../services/observationService';
import biomarkerService from '../../services/biomarkerService';
import type { Biomarker, Unit, AllowedState } from '../../types/biomarker';
import type { Observation } from '../../types/observation';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  observation: Observation | null;
  biomarker: Biomarker;
  onSuccess?: () => void;
}

/**
 * Edit modal for a single biomarker observation (a "record" / "reading").
 *
 * Patches the mutable fields only: value (QUANTITY numeric or STATE code),
 * unit, effective_datetime, method, comment. The biomarker definition,
 * patient, examination linkage, and code are identity fields — they're
 * never changed here (re-assigning a biomarker = delete + re-create).
 *
 * Mirrors the visual language of AddBiomarkerForm but is a focused edit
 * form (no catalog search, no create-definition CTA, no interpretation
 * toggle).
 */
export const EditBiomarkerReadingModal: React.FC<Props> = ({
  isOpen,
  onClose,
  observation,
  biomarker,
  onSuccess,
}) => {
  const { t } = useTranslation();
  const [units, setUnits] = useState<Unit[]>([]);
  const [loading, setLoading] = useState(false);
  const isState = biomarker.value_type === 'state';

  const [formData, setFormData] = useState({
    value: '',
    unit: '',
    note: '',
    measuredDate: '',
    measuredTime: '',
    method: '',
  });

  // Hydrate form fields from the observation when it changes.
  useEffect(() => {
    if (!observation) return;
    const eff = observation.effective_datetime ? new Date(observation.effective_datetime) : new Date();
    const pad = (n: number) => String(n).padStart(2, '0');
    const measuredDate = `${eff.getFullYear()}-${pad(eff.getMonth() + 1)}-${pad(eff.getDate())}`;
    const measuredTime = `${pad(eff.getHours())}:${pad(eff.getMinutes())}`;

    // Extract the current value: QUANTITY → numeric, STATE → code.
    let value = '';
    if (isState) {
      const coding = observation.value_codeable_concept?.coding?.[0];
      value = coding?.code || '';
    } else {
      value = observation.value_quantity?.value != null
        ? String(observation.value_quantity.value)
        : (observation.raw_value != null ? String(observation.raw_value) : '');
    }

    setFormData({
      value,
      unit: observation.value_quantity?.unit || biomarker.preferred_unit_symbol || '',
      note: observation.comment || '',
      measuredDate,
      measuredTime,
      method: observation.method || '',
    });
  }, [observation, biomarker, isState]);

  // Load units for the UnitSelector.
  useEffect(() => {
    biomarkerService.getUnits().then(setUnits).catch(() => {});
  }, []);

  // Esc-to-close + body scroll lock.
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
    };
  }, [isOpen, onClose]);

  if (!isOpen || !observation) return null;

  const handleSubmit = async () => {
    if (!formData.value) return;
    setLoading(true);
    try {
      const updates: Record<string, any> = {};

      if (isState) {
        // Resolve the picked state slug → code + system from the
        // biomarker's allowed_states catalog.
        const pickedState = (biomarker.allowed_states ?? []).find(
          (s: AllowedState) => s.state_slug === formData.value || s.code === formData.value || s.display === formData.value,
        );
        updates.value_codeable_concept = {
          coding: [{
            code: pickedState?.code ?? formData.value,
            system: pickedState?.system ?? 'http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation',
            display: pickedState?.display ?? formData.value,
          }],
        };
      } else {
        updates.value_quantity = {
          value: parseFloat(formData.value),
          unit: formData.unit || biomarker.preferred_unit_symbol,
          system: 'http://unitsofmeasure.org',
          code: formData.unit || biomarker.preferred_unit_symbol,
        };
        updates.raw_value = parseFloat(formData.value);
      }

      // Timestamp (combine date + time → UTC ISO).
      if (formData.measuredDate) {
        const combined = new Date(`${formData.measuredDate}T${formData.measuredTime || '00:00'}`);
        updates.effective_datetime = combined.toISOString();
      }

      if (formData.note !== (observation.comment || '')) {
        updates.note = formData.note ? [{ text: formData.note }] : [];
      }
      if (formData.method !== (observation.method || '')) {
        updates.method = formData.method.trim() || null;
      }

      await updateObservation(observation.id!, updates);
      toast.success(t('biomarkers.edit_reading.saved', { defaultValue: 'Reading updated' }));
      onSuccess?.();
      onClose();
    } catch (err) {
      console.error('Failed to update observation', err);
      toast.error(t('biomarkers.edit_reading.save_error', { defaultValue: 'Failed to update reading' }));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[1000] flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm animate-in fade-in duration-200"
      role="dialog"
      aria-modal="true"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-white dark:bg-dark-surface w-full max-w-xl rounded-3xl shadow-2xl border border-gray-100 dark:border-dark-border overflow-hidden flex flex-col max-h-[90vh] animate-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="px-8 py-6 border-b border-gray-50 dark:border-dark-border flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-blue-50 dark:bg-blue-900/30 rounded-xl">
              <FlaskConical className="w-6 h-6 text-blue-600 dark:text-blue-400" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-gray-900 dark:text-dark-text">
                {t('biomarkers.edit_reading.title', { defaultValue: 'Edit Reading' })}
              </h2>
              <p className="text-[10px] text-gray-400 font-black uppercase tracking-widest mt-0.5">
                {biomarker.name}
              </p>
            </div>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors">
            <X className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        {/* Body */}
        <form className="flex-1 min-h-0 overflow-y-auto p-8 space-y-6" onSubmit={(e) => { e.preventDefault(); handleSubmit(); }}>
          {/* Biomarker context badge */}
          <div className="p-4 bg-blue-50 dark:bg-blue-900/10 border border-blue-100 dark:border-blue-900/30 rounded-2xl">
            <div className="flex items-center space-x-3">
              <div className="p-2 bg-blue-600 text-white rounded-lg">
                <Activity className="w-4 h-4" />
              </div>
              <div className="min-w-0">
                <h4 className="font-bold text-blue-900 dark:text-blue-300 truncate">{biomarker.name}</h4>
                <p className="text-[10px] text-blue-600 dark:text-blue-400 uppercase font-black tracking-widest">
                  {biomarker.slug}
                  {!isState && biomarker.reference_range_min != null && biomarker.reference_range_max != null
                    ? ` · ${biomarker.reference_range_min}–${biomarker.reference_range_max} ${biomarker.preferred_unit_symbol || ''}`
                    : ''}
                </p>
              </div>
            </div>
          </div>

          {/* Value + Unit */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="space-y-3">
              <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">
                {isState
                  ? t('biomarkers.state_value', { defaultValue: 'State' })
                  : t('examination_detail.add_biomarker.value', { defaultValue: 'Value' })}
              </label>
              {isState ? (
                <select
                  className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 font-bold"
                  value={formData.value}
                  onChange={(e) => setFormData({ ...formData, value: e.target.value })}
                  required
                >
                  <option value="">{t('biomarkers.state_value_placeholder', { defaultValue: 'Select a state…' })}</option>
                  {(biomarker.allowed_states ?? []).map((s: AllowedState) => (
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
                  onChange={(e) => setFormData({ ...formData, value: e.target.value })}
                  required
                />
              )}
            </div>
            <div className="space-y-3">
              <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">
                {t('examination_detail.add_biomarker.unit', { defaultValue: 'Unit' })}
              </label>
              {isState ? (
                <p className="px-4 py-3 text-sm text-gray-400 dark:text-dark-muted italic">
                  {t('biomarker_catalog.state_no_unit', { defaultValue: 'State biomarkers carry no unit (categorical values are unitless).' })}
                </p>
              ) : (
                <UnitSelector
                  units={units}
                  selectedSymbol={formData.unit}
                  onSelect={(u) => setFormData(prev => ({ ...prev, unit: u.symbol }))}
                  onUnitsUpdated={setUnits}
                  placeholder={t('examination_detail.add_biomarker.select_unit', { defaultValue: 'Select Unit' })}
                />
              )}
            </div>
          </div>

          {/* Date + Method */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="space-y-3">
              <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1 flex items-center">
                <Calendar className="w-3 h-3 mr-2" />
                {t('biomarkers.log_reading.measured_at', { defaultValue: 'Measurement Date' })}
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
                {t('biomarkers.log_reading.method', { defaultValue: 'Method (optional)' })}
              </label>
              <input
                type="text"
                placeholder={t('biomarkers.log_reading.method_placeholder', { defaultValue: 'e.g. Fingerstick, Home BP cuff, Lab draw' })}
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 font-medium"
                value={formData.method}
                onChange={(e) => setFormData({ ...formData, method: e.target.value })}
              />
            </div>
          </div>

          {/* Note */}
          <div className="space-y-3">
            <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1 flex items-center">
              <Info className="w-3 h-3 mr-2" />
              {t('examination_detail.add_biomarker.observations', { defaultValue: 'Observations / Notes' })}
            </label>
            <textarea
              rows={3}
              className="w-full px-4 py-4 bg-gray-50 dark:bg-dark-bg border-none rounded-2xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 resize-none text-sm"
              placeholder={t('examination_detail.add_biomarker.observations_placeholder', { defaultValue: 'Clinical notes or comments...' })}
              value={formData.note}
              onChange={(e) => setFormData({ ...formData, note: e.target.value })}
            />
          </div>
        </form>

        {/* Footer */}
        <div className="px-8 py-6 bg-gray-50 dark:bg-dark-bg/50 border-t border-gray-50 dark:border-dark-border flex items-center justify-end space-x-4">
          <button
            type="button"
            onClick={onClose}
            disabled={loading}
            className="px-6 py-2.5 text-sm font-bold text-gray-500 hover:text-gray-700 dark:text-dark-muted transition-colors uppercase tracking-widest disabled:opacity-50"
          >
            {t('common.cancel', { defaultValue: 'Cancel' })}
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={loading || !formData.value}
            className="px-8 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-xl font-bold text-sm shadow-lg shadow-blue-500/20 transition-all flex items-center space-x-2 uppercase tracking-widest"
          >
            {loading ? (
              <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent" />
            ) : (
              <Save className="w-4 h-4" />
            )}
            <span>{t('common.save', { defaultValue: 'Save' })}</span>
          </button>
        </div>
      </div>
    </div>
  );
};
