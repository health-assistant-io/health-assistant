import React, { useState, useEffect, forwardRef, useImperativeHandle, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import { Search, Info, Calendar, Clock } from 'lucide-react';
import { AIAssistButton } from '../ui/AIAssistButton';
import { DatePicker } from '../ui/DatePicker';
import { TimeList } from '../ui/TimeList';
import { MedicationRecord, MedicationTiming, type MedicationStatus } from '../../services/medicationService';
import { normalizeMedicationStatus } from '../../utils/medicationStatus';
import { CatalogItemPicker } from '../catalog/CatalogItemPicker';
import { searchCatalogs } from '../../services/catalogService';
import type { CatalogSelection } from '../../types/catalog';
import { InstanceField } from '../instances/InstanceField';
import '../../features/instances/adapters'; // registers the instance adapters
import { LinksSection } from '../ai/hitl/LinksSection';
import { RecordFormFooter } from '../records/RecordFormFooter';

export interface MedicationFormPrefill {
  name?: string;
  catalog_id?: string | null;
  matched?: boolean;
  dosage?: string;
  frequency_label?: string;
  reason?: string;
  note?: string;
  start_date?: string;
  end_date?: string;
  status?: string;
  /** AI-proposed graph links (attached to the catalog entry, NOT the
   *  prescription). Persisted by the caller via createLinksFor('medication',
   *  catalog_id, links) after the prescription is committed. */
  links?: CatalogSelection[];
}

export interface MedicationFormPayload {
  status: string;
  dosage: string | null;
  frequency: MedicationTiming;
  start_date?: string;
  end_date?: string;
  reason: string | null;
  note: string | null;
  code: { text: string; catalog_id?: string };
  /** Optional examination to link this medication to at create/edit time. */
  examination_id?: string | null;
  /** User-edited graph links — attached to the medication catalog entry.
   *  The caller's onSubmit persists them via createLinksFor('medication',
   *  catalog_id, links) AFTER the prescription is committed. */
  links: CatalogSelection[];
}

export interface MedicationFormHandle {
  submit: () => void;
}

interface MedicationFormProps {
  patientId: string;
  medication?: MedicationRecord;
  prefill?: MedicationFormPrefill;
  onSubmit: (payload: MedicationFormPayload) => Promise<void>;
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
   * this catalog entry. Applies when `catalog_id` CHANGES and the picker does
   * not already hold it; never re-applies after the user clears the chip; a
   * remount with the same value is a no-op (the ref seeds from the prop).
   */
  catalogSelection?: { catalog_id: string; text: string };
}

const PRESETS = [
  { key: 'once_daily', type: 'daily', frequency: 1, period: 1, period_unit: 'day', times: ['09:00'] },
  { key: 'twice_daily', type: 'daily', frequency: 2, period: 1, period_unit: 'day', times: ['09:00', '21:00'] },
  { key: 'three_times', type: 'daily', frequency: 3, period: 1, period_unit: 'day', times: ['08:00', '14:00', '20:00'] },
  { key: 'every_8_hours', type: 'interval', frequency: 1, period: 8, period_unit: 'hour', times: ['06:00', '14:00', '22:00'] },
  { key: 'weekly', type: 'weekly', frequency: 1, period: 1, period_unit: 'week', times: ['09:00'] },
];

export const MedicationForm = forwardRef<MedicationFormHandle, MedicationFormProps>(
  function MedicationForm(
    {
      patientId,
      medication,
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
    },
    ref
  ) {
    const { t } = useTranslation();
    const [selection, setSelection] = useState<CatalogSelection[]>([]);
    const [loading, setLoading] = useState(false);
    const [links, setLinks] = useState<CatalogSelection[]>([]);

    const [formData, setFormData] = useState({
      status: 'active' as MedicationStatus,
      dosage: '',
      start_date: new Date().toISOString().split('T')[0],
      end_date: '',
      reason: '',
      note: '',
      examination_id: '' as string
    });

    const [timing, setTiming] = useState<MedicationTiming>({
      type: 'daily',
      frequency: 1,
      period: 1,
      period_unit: 'day',
      days_of_week: [],
      time_of_day: ['09:00'],
      as_needed: false
    });

    useEffect(() => {
      if (medication) {
        setFormData({
          status: normalizeMedicationStatus(medication.status),
          dosage: medication.dosage || '',
          start_date: medication.start_date ? medication.start_date.split('T')[0] : '',
          end_date: medication.end_date ? medication.end_date.split('T')[0] : '',
          reason: medication.reason || '',
          note: medication.note || '',
          examination_id: medication.examination_id || ''
        });
        if (medication.frequency) {
          setTiming(medication.frequency);
        }
        setSelection([]);
        setLinks([]);
      } else if (prefill) {
        setFormData(prev => ({
          ...prev,
          status: prefill.status ? normalizeMedicationStatus(prefill.status) : 'active',
          dosage: prefill.dosage || '',
          start_date: prefill.start_date ? prefill.start_date.split('T')[0] : new Date().toISOString().split('T')[0],
          end_date: prefill.end_date ? prefill.end_date.split('T')[0] : '',
          reason: prefill.reason || '',
          note: prefill.note || ''
        }));
        setLinks(Array.isArray(prefill.links) ? prefill.links : []);

        if (prefill.frequency_label) {
          const preset = PRESETS.find(p => t(`medications.modal.presets.${p.key}`).toLowerCase() === prefill.frequency_label?.toLowerCase());
          if (preset) {
            setTiming({
              ...timing,
              type: preset.type as any,
              frequency: preset.frequency,
              period: preset.period,
              period_unit: preset.period_unit as any,
              time_of_day: preset.times,
              display: t(`medications.modal.presets.${preset.key}`)
            });
          }
        }

        if (prefill.matched && prefill.catalog_id) {
          setSelection([{
            type: 'medication',
            id: prefill.catalog_id,
            label: prefill.name || ''
          }]);
        }
      } else {
        setFormData({
          status: 'active',
          dosage: '',
          start_date: new Date().toISOString().split('T')[0],
          end_date: '',
          reason: '',
          note: '',
          examination_id: ''
        });
        setTiming({
          type: 'daily',
          frequency: 1,
          period: 1,
          period_unit: 'day',
          days_of_week: [],
          time_of_day: ['09:00'],
          as_needed: false
        });
        setSelection([]);
        setLinks([]);
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [medication, prefill]);

    // One-shot programmatic pick (HITL confirm-after-create). The ref seeds
    // from the prop so a remount with the same value is a no-op, and the
    // value never re-applies once the user has cleared the chip.
    const appliedCatalogRef = useRef<string | null>(catalogSelection?.catalog_id ?? null);
    useEffect(() => {
      const id = catalogSelection?.catalog_id;
      if (!id || appliedCatalogRef.current === id) return;
      appliedCatalogRef.current = id;
      if (selection.some((s) => s.id === id)) return;
      setSelection([{ type: 'medication', id, label: catalogSelection?.text ?? '' }]);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [catalogSelection]);

    const handleSubmit = async (e?: React.FormEvent) => {
      if (e) e.preventDefault();
      if (loading || (!medication && selection.length === 0)) return;
      setLoading(true);

      try {
        const payload: MedicationFormPayload = {
          status: formData.status,
          dosage: formData.dosage || null,
          frequency: timing,
          start_date: formData.start_date || undefined,
          end_date: formData.end_date || undefined,
          reason: formData.reason || null,
          note: formData.note || null,
          code: medication ? medication.code : {
            text: selection[0]?.label ?? '',
            catalog_id: selection[0]?.id
          },
          examination_id: formData.examination_id || undefined,
          links,
        };

        await onSubmit(payload);
      } catch (err) {
        console.error("Failed to save medication form", err);
      } finally {
        setLoading(false);
      }
    };

    useImperativeHandle(ref, () => ({ submit: () => handleSubmit() }));

    const canSubmit =
      !loading && (selection.length > 0 || Boolean(medication));

    const reportedState = useRef('');
    useEffect(() => {
      const key = `${canSubmit}|${loading}`;
      if (reportedState.current === key) return;
      reportedState.current = key;
      onStateChange?.({ canSubmit, loading });
    }, [canSubmit, loading, onStateChange]);

    const toggleDay = (day: string) => {
      const current = timing.days_of_week || [];
      if (current.includes(day)) {
        setTiming({ ...timing, days_of_week: current.filter(d => d !== day) });
      } else {
        setTiming({ ...timing, days_of_week: [...current, day], type: 'specific_days' });
      }
    };

    const setTimes = (next: string[]) => {
      setTiming({ ...timing, time_of_day: next });
    };

    return (
      <form id={formId} onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col">
        <div className="flex-1 min-h-0 overflow-y-auto p-8 space-y-8 custom-scrollbar">
          {/* Substance Selection */}
          {!medication && (
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-2">
                <label className="text-xs font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest px-1 flex items-center">
                  <Search className="w-3 h-3 mr-2" />
                  {t('medications.modal.select_from_catalog')}
                </label>
                <AIAssistButton
                  taskType="fill_medication_form"
                  context={{ patientId }}
                  onSuggestedData={async (data) => {
                    setFormData(prev => ({
                      ...prev,
                      dosage: data.dosage || prev.dosage,
                      reason: data.reason || prev.reason,
                      note: data.note || prev.note
                    }));
                    if (data.frequency_label) {
                      const preset = PRESETS.find(p => t(`medications.modal.presets.${p.key}`).toLowerCase() === data.frequency_label.toLowerCase());
                      if (preset) {
                        setTiming({
                          ...timing,
                          type: preset.type as any,
                          frequency: preset.frequency,
                          period: preset.period,
                          period_unit: preset.period_unit as any,
                          time_of_day: preset.times,
                          display: t(`medications.modal.presets.${preset.key}`)
                        });
                      } else {
                        setFormData(prev => ({...prev, note: `${prev.note ? prev.note + '\n' : ''}Suggested Frequency: ${data.frequency_label}`}));
                      }
                    }
                    if (data.medication_name && selection.length === 0) {
                      try {
                        const resp = await searchCatalogs(data.medication_name, { types: 'medication', limit: 1 });
                        if (resp.results.length > 0) {
                          const hit = resp.results[0];
                          setSelection([{ type: hit.type, id: hit.id, label: hit.label }]);
                        } else {
                          // Unmatched name: the picker stays empty — definitions
                          // are created from the browse dialog (Browse → New).
                          toast.info(
                            t('medications.modal.not_in_catalog', '{{name}} is not in the catalog — define it via Browse → New.', { name: data.medication_name }),
                          );
                        }
                      } catch {
                        // A FAILED search is not an unmatched name — only an
                        // empty result may claim "not in the catalog".
                        toast.error(
                          t('catalogs.search_failed', 'Could not search the catalog — try again.'),
                        );
                      }
                    }
                  }}
                />
              </div>

              <CatalogItemPicker
                mode="single"
                allowedTypes={['medication']}
                value={selection}
                onChange={setSelection}
                placeholder={t('medications.modal.search_placeholder')}
                displayMode="cards"
                block
                allowCreate
              />
            </div>
          )}

          {medication && (
             <div className="p-6 bg-gray-50 dark:bg-dark-bg rounded-2xl border border-gray-100 dark:border-dark-border">
                <p className="text-xs font-bold text-gray-400 uppercase tracking-widest mb-1">{t('medications.modal.editing_record_for')}</p>
                <h3 className="text-xl font-bold text-gray-900 dark:text-dark-text">{medication.code.text}</h3>
             </div>
          )}

          {/* Timing & Frequency Section */}
          <div className="space-y-6">
             <div className="flex items-center space-x-2 border-b border-gray-50 dark:border-dark-border pb-2">
                <Clock className="w-4 h-4 text-blue-500" />
                <h3 className="text-sm font-bold text-gray-900 dark:text-dark-text tracking-tight">{t('medications.modal.prescription_schedule')}</h3>
             </div>

             {/* Presets */}
             <div className="flex flex-wrap gap-2">
                {PRESETS.map((p, i) => (
                  <button
                    key={i}
                    type="button"
                    onClick={() => setTiming({
                      ...timing,
                      type: p.type as any,
                      frequency: p.frequency,
                      period: p.period,
                      period_unit: p.period_unit as any,
                      time_of_day: p.times,
                      display: t(`medications.modal.presets.${p.key}`)
                    })}
                    className="px-3 py-1.5 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl text-xs font-bold text-gray-500 hover:bg-blue-50 dark:hover:bg-blue-900/20 hover:text-blue-600 dark:hover:text-blue-400 transition-all"
                  >
                    {t(`medications.modal.presets.${p.key}`)}
                  </button>
                ))}
             </div>

             <div className="grid grid-cols-1 md:grid-cols-2 gap-8 bg-gray-50/50 dark:bg-dark-bg/30 p-6 rounded-2xl border border-gray-50 dark:border-dark-border">
                <div className="space-y-6">
                  <div className="space-y-3">
                    <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1">{t('medications.modal.repeat_every')}</label>
                    <div className="flex items-center space-x-2">
                      <input 
                        type="number" 
                        min="1"
                        className="w-16 px-3 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-lg text-sm font-bold focus:ring-2 focus:ring-blue-500 outline-hidden dark:text-dark-text"
                        value={timing.period}
                        onChange={e => setTiming({...timing, period: parseInt(e.target.value) || 1})}
                      />
                      <select 
                        className="flex-1 px-3 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-lg text-sm font-bold focus:ring-2 focus:ring-blue-500 outline-hidden dark:text-dark-text"
                        value={timing.period_unit}
                        onChange={e => setTiming({...timing, period_unit: e.target.value as any})}
                      >
                        <option value="day">{t('medications.modal.days')}</option>
                        <option value="week">{t('medications.modal.weeks')}</option>
                        <option value="month">{t('medications.modal.months')}</option>
                        <option value="hour">{t('medications.modal.hours')}</option>
                      </select>
                    </div>
                  </div>

                  {timing.period_unit === 'week' && (
                    <div className="space-y-3">
                       <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1">{t('medications.modal.specific_days')}</label>
                       <div className="flex justify-between">
                          {['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'].map(d => (
                            <button
                              key={d}
                              type="button"
                              onClick={() => toggleDay(d)}
                              className={`w-8 h-8 rounded-full text-[10px] font-bold uppercase transition-all ${timing.days_of_week?.includes(d) ? 'bg-blue-600 text-white shadow-md' : 'bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border text-gray-400'}`}
                            >
                              {d[0]}
                            </button>
                          ))}
                       </div>
                    </div>
                  )}

                  <div className="flex items-center space-x-3">
                    <input 
                      type="checkbox" 
                      id="as_needed" 
                      className="w-4 h-4 rounded-sm border-gray-300 text-blue-600 focus:ring-blue-500"
                      checked={timing.as_needed}
                      onChange={e => setTiming({...timing, as_needed: e.target.checked})}
                    />
                    <label htmlFor="as_needed" className="text-xs font-bold text-gray-600 dark:text-dark-text cursor-pointer">{t('medications.modal.as_needed_label')}</label>
                  </div>
                </div>

                <div>
                   <TimeList
                     label={t('medications.modal.scheduled_times')}
                     addLabel={t('medications.modal.add_time')}
                     emptyLabel={t('medications.modal.no_times_yet', 'No times scheduled yet.')}
                     value={timing.time_of_day ?? []}
                     onChange={setTimes}
                   />
                </div>
             </div>
          </div>

          {/* Form Fields */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pt-4">
            <div className="space-y-4">
              <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1">{t('medications.modal.current_status')}</label>
              <select
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 outline-hidden"
                value={formData.status}
                onChange={e => setFormData({...formData, status: e.target.value as any})}
              >
                <option value="active">{t('medications.modal.status.active')}</option>
                <option value="inactive">{t('medications.modal.status.inactive')}</option>
                <option value="completed">{t('medications.modal.status.completed')}</option>
                <option value="cancelled">{t('medications.modal.status.cancelled')}</option>
                <option value="on-hold">{t('medications.modal.status.on_hold')}</option>
                <option value="stopped">{t('medications.modal.status.stopped')}</option>
                <option value="intended">{t('medications.modal.status.intended')}</option>
                <option value="entered-in-error">{t('medications.modal.status.entered_in_error')}</option>
              </select>
            </div>

            <div className="space-y-4">
              <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1">{t('medications.modal.dosage')}</label>
              <input
                type="text"
                placeholder={t('medications.modal.dosage_placeholder')}
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 outline-hidden"
                value={formData.dosage}
                onChange={e => setFormData({...formData, dosage: e.target.value})}
              />
            </div>

            <div className="space-y-4">
              <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1">{t('medications.indications')}</label>
              <input
                type="text"
                placeholder={t('medications.modal.reason_placeholder')}
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 outline-hidden"
                value={formData.reason}
                onChange={e => setFormData({...formData, reason: e.target.value})}
              />
            </div>

            <div className="space-y-2">
              <InstanceField
                label={t('medications.linked_examination', 'Linked examination')}
                allowedTypes={['examination']}
                patientId={patientId}
                mode="single"
                displayMode="cards"
                value={
                  formData.examination_id
                    ? [{ type: 'examination', id: formData.examination_id }]
                    : []
                }
                onChange={(sel) =>
                  setFormData({ ...formData, examination_id: sel[0]?.id ?? '' })
                }
                placeholder={t('medications.link_examination', 'Link an examination…')}
              />
            </div>

            <div className="space-y-2">
              <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1 flex items-center">
                <Calendar className="w-3 h-3 mr-2" />
                {t('medications.modal.start_date')}
              </label>
              <DatePicker
                placeholder={t('common.select_date', 'Select date')}
                value={formData.start_date}
                onChange={date => setFormData({...formData, start_date: date})}
              />
            </div>

            <div className="space-y-2">
              <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1 flex items-center">
                <Calendar className="w-3 h-3 mr-2" />
                {t('medications.modal.end_date_opt')}
              </label>
              <DatePicker
                placeholder={t('common.select_date', 'Select date')}
                value={formData.end_date}
                onChange={date => setFormData({...formData, end_date: date})}
              />
            </div>
          </div>

          <div className="space-y-4">
            <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1 flex items-center">
              <Info className="w-3 h-3 mr-2" />
              {t('medications.modal.additional_notes')}
            </label>
            <textarea
              rows={3}
              className="w-full px-4 py-4 bg-gray-50 dark:bg-dark-bg border-none rounded-2xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 outline-hidden resize-none"
              placeholder={t('medications.modal.notes_placeholder')}
              value={formData.note}
              onChange={e => setFormData({...formData, note: e.target.value})}
            />
          </div>

          {/* AI-proposed graph links attached to the catalog entry.
              Shown only when the matrix offers destinations for medications. */}
          <LinksSection
            srcType="medication"
            value={links}
            onChange={setLinks}
            hideWhenEmpty
          />
        </div>

        {/* Footer */}
        {showActions && (
          <div className="flex shrink-0 items-center border-t border-gray-50 bg-gray-50 px-8 py-6 dark:border-dark-border dark:bg-dark-bg/50">
            <RecordFormFooter
              formId={formId}
              canSubmit={canSubmit}
              loading={loading}
              submitLabel={
                submitLabel ??
                (medication
                  ? t('medications.modal.update_record')
                  : t('medications.modal.save_medication'))
              }
              cancelLabel={t('common.cancel')}
              onCancel={onCancel}
              rejectLabel={rejectLabel}
              onReject={onReject}
            />
          </div>
        )}
      </form>
    );
  }
);
