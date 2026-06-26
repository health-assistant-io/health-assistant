import React, { useState, useEffect, forwardRef, useImperativeHandle } from 'react';
import { useTranslation } from 'react-i18next';
import { Search, Plus, Save, Info, Calendar, Pill, Clock, X } from 'lucide-react';
import { AIAssistButton } from '../ui/AIAssistButton';
import { DatePicker } from '../ui/DatePicker';
import { 
  searchMedicationCatalog, 
  MedicationCatalogEntry, 
  MedicationRecord,
  MedicationTiming
} from '../../services/medicationService';

export interface MedicationFormPrefill {
  name?: string;
  catalog_id?: string | null;
  matched?: boolean;
  is_new?: boolean;
  indications?: string | null;
  side_effects?: string[];
  contraindications?: string | null;
  dosage_info?: string | null;
  dosage?: string;
  frequency_label?: string;
  reason?: string;
  note?: string;
  start_date?: string;
  end_date?: string;
  status?: string;
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
  indications?: string;
  side_effects?: string[];
  is_new_catalog_entry?: boolean;
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
  showHeader?: boolean;
  showActions?: boolean;
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
      showHeader = true,
      showActions = true,
    },
    ref
  ) {
    const { t } = useTranslation();
    const [searchTerm, setSearchTerm] = useState('');
    const [catalogResults, setCatalogResults] = useState<MedicationCatalogEntry[]>([]);
    const [selectedCatalogItem, setSelectedCatalogItem] = useState<MedicationCatalogEntry | null>(null);
    const [loading, setLoading] = useState(false);
    const [searching, setSearching] = useState(false);
    const [isAddingNew, setIsAddingNew] = useState(false);

    const [formData, setFormData] = useState({
      status: 'active' as 'active' | 'completed' | 'entered-in-error' | 'intended' | 'stopped' | 'on-hold' | 'unknown',
      dosage: '',
      indications: '',
      side_effects: [] as string[],
      start_date: new Date().toISOString().split('T')[0],
      end_date: '',
      reason: '',
      note: ''
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
          status: medication.status as any,
          dosage: medication.dosage || '',
          indications: '',
          side_effects: [],
          start_date: medication.start_date ? medication.start_date.split('T')[0] : '',
          end_date: medication.end_date ? medication.end_date.split('T')[0] : '',
          reason: medication.reason || '',
          note: medication.note || ''
        });
        if (medication.frequency) {
          setTiming(medication.frequency);
        }
        setSearchTerm(medication.code.text);
        setIsAddingNew(false);
      } else if (prefill) {
        setFormData(prev => ({
          ...prev,
          status: (prefill.status as any) || 'active',
          dosage: prefill.dosage || '',
          indications: prefill.indications || '',
          side_effects: prefill.side_effects || [],
          start_date: prefill.start_date ? prefill.start_date.split('T')[0] : new Date().toISOString().split('T')[0],
          end_date: prefill.end_date ? prefill.end_date.split('T')[0] : '',
          reason: prefill.reason || '',
          note: prefill.note || ''
        }));
        
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
        
        if (prefill.name) setSearchTerm(prefill.name);
        
        if (prefill.matched && prefill.catalog_id) {
          setSelectedCatalogItem({
            id: prefill.catalog_id,
            name: prefill.name || '',
            indications: prefill.indications || undefined,
            side_effects: prefill.side_effects || [],
            contraindications: prefill.contraindications || undefined,
            dosage_info: prefill.dosage_info || undefined,
            is_custom: false
          });
          setIsAddingNew(false);
        } else if (prefill.is_new) {
          setIsAddingNew(true);
        }
      } else {
        setFormData({
          status: 'active',
          dosage: '',
          indications: '',
          side_effects: [],
          start_date: new Date().toISOString().split('T')[0],
          end_date: '',
          reason: '',
          note: ''
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
        setSearchTerm('');
        setSelectedCatalogItem(null);
        setIsAddingNew(false);
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [medication, prefill]);

    useEffect(() => {
      // Don't search if we are editing an existing medication, or if we have a prefill match, or defining new
      if (searchTerm.length > 2 && !medication && !isAddingNew && (!prefill?.matched || searchTerm !== prefill?.name)) {
        const delayDebounceFn = setTimeout(() => {
          setSearching(true);
          searchMedicationCatalog(searchTerm).then(results => {
            setCatalogResults(results);
            setSearching(false);
          });
        }, 300);
        return () => clearTimeout(delayDebounceFn);
      } else {
        setCatalogResults([]);
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [searchTerm, medication, isAddingNew, prefill]);

    const handleSubmit = async (e?: React.FormEvent) => {
      if (e) e.preventDefault();
      if (loading || (!selectedCatalogItem && !searchTerm && !medication)) return;
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
            text: searchTerm,
            catalog_id: selectedCatalogItem?.id
          },
          indications: isAddingNew ? formData.indications : undefined,
          side_effects: isAddingNew ? formData.side_effects : undefined,
          is_new_catalog_entry: isAddingNew
        };

        await onSubmit(payload);
      } catch (err) {
        console.error("Failed to save medication form", err);
      } finally {
        setLoading(false);
      }
    };

    useImperativeHandle(ref, () => ({ submit: () => handleSubmit() }));

    const addSideEffect = (effect: string) => {
      if (effect && !formData.side_effects.includes(effect)) {
        setFormData({ ...formData, side_effects: [...formData.side_effects, effect] });
      }
    };

    const removeSideEffect = (index: number) => {
      setFormData({
        ...formData,
        side_effects: formData.side_effects.filter((_, i) => i !== index)
      });
    };

    const toggleDay = (day: string) => {
      const current = timing.days_of_week || [];
      if (current.includes(day)) {
        setTiming({ ...timing, days_of_week: current.filter(d => d !== day) });
      } else {
        setTiming({ ...timing, days_of_week: [...current, day], type: 'specific_days' });
      }
    };

    const addTime = () => {
      setTiming({ ...timing, time_of_day: [...(timing.time_of_day || []), '09:00'] });
    };

    const removeTime = (index: number) => {
      setTiming({ ...timing, time_of_day: timing.time_of_day?.filter((_, i) => i !== index) });
    };

    const updateTime = (index: number, val: string) => {
      const newTimes = [...(timing.time_of_day || [])];
      newTimes[index] = val;
      setTiming({ ...timing, time_of_day: newTimes });
    };

    return (
      <div className="flex flex-col flex-1 min-h-0">
        {/* Header */}
        {showHeader && (
          <div className="px-8 py-6 border-b border-gray-50 dark:border-dark-border flex items-center justify-between shrink-0 bg-white dark:bg-dark-surface">
            <div className="flex items-center space-x-3">
              <div className="p-2 bg-blue-50 dark:bg-blue-900/30 rounded-xl">
                <Pill className="w-6 h-6 text-blue-600 dark:text-blue-400" />
              </div>
              <div>
                <h2 className="text-xl font-bold text-gray-900 dark:text-dark-text">
                  {medication ? t('medications.modal.update_title') : t('medications.modal.new_title')}
                </h2>
                <p className="text-xs text-gray-500 dark:text-dark-muted font-medium uppercase tracking-widest mt-0.5">
                  {t('medications.modal.clinical_records')}
                </p>
              </div>
            </div>
            <div className="flex items-center space-x-4">
              {!medication && (
                <AIAssistButton 
                  taskType="fill_medication_form" 
                  context={{ patientId }} 
                  onSuggestedData={async (data) => {
                    if (data.medication_name && !searchTerm) {
                      setSearchTerm(data.medication_name);
                    }
                    
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
                  }}
                />
              )}
              {onCancel && (
                <button type="button" onClick={onCancel} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors">
                  <X className="w-5 h-5 text-gray-400" />
                </button>
              )}
            </div>
          </div>
        )}

        <form onSubmit={handleSubmit} className="flex-1 min-h-0 overflow-y-auto p-8 space-y-8 custom-scrollbar">
          {/* Substance Selection */}
          {!medication && (
            <div className="space-y-4">
              <label className="text-xs font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest px-1 flex items-center">
                <Search className="w-3 h-3 mr-2" />
                {t('medications.modal.select_from_catalog')}
              </label>
              <div className="relative">
                <div className="flex gap-2">
                  <input
                    type="text"
                    disabled={isAddingNew}
                    placeholder={t('medications.modal.search_placeholder')}
                    className="flex-1 px-6 py-4 bg-gray-50 dark:bg-dark-bg border-none rounded-2xl text-gray-900 dark:text-dark-text placeholder-gray-400 focus:ring-2 focus:ring-blue-500/20 transition-all font-medium disabled:opacity-50"
                    value={searchTerm}
                    onChange={(e) => {
                      setSearchTerm(e.target.value);
                      if (selectedCatalogItem) setSelectedCatalogItem(null);
                    }}
                    autoFocus
                  />
                  {isAddingNew && (
                    <button 
                      type="button"
                      onClick={() => { setIsAddingNew(false); setSearchTerm(''); }}
                      className="px-4 bg-gray-100 dark:bg-dark-bg rounded-2xl text-gray-400 hover:text-gray-600 transition-colors"
                    >
                      <X className="w-5 h-5" />
                    </button>
                  )}
                </div>
                {searching && (
                  <div className="absolute right-4 top-4">
                    <div className="animate-spin rounded-full h-5 w-5 border-2 border-blue-500 border-t-transparent" />
                  </div>
                )}
              </div>

              {catalogResults.length > 0 && !selectedCatalogItem && !isAddingNew && (
                <div className="bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-2xl shadow-xl overflow-hidden animate-in slide-in-from-top-2">
                  {catalogResults.map(item => (
                    <button
                      key={item.id}
                      type="button"
                      className="w-full px-6 py-4 text-left hover:bg-blue-50 dark:hover:bg-blue-900/10 transition-colors border-b border-gray-50 dark:border-dark-border last:border-0"
                      onClick={() => {
                        setSelectedCatalogItem(item);
                        setSearchTerm(item.name);
                        setCatalogResults([]);
                      }}
                    >
                      <p className="font-bold text-gray-900 dark:text-dark-text">{item.name}</p>
                      <p className="text-xs text-gray-500 truncate mt-0.5">{item.indications || item.description}</p>
                    </button>
                  ))}
                  
                  <button
                    type="button"
                    onClick={() => setIsAddingNew(true)}
                    className="w-full text-left px-6 py-5 bg-blue-50/50 dark:bg-blue-900/10 hover:bg-blue-50 dark:hover:bg-blue-900/20 flex items-center space-x-3 text-blue-600 border-t border-gray-50 dark:border-dark-border"
                  >
                    <div className="p-2 bg-blue-600 text-white rounded-xl">
                      <Plus className="w-4 h-4" />
                    </div>
                    <div>
                      <p className="text-sm font-bold italic">"{searchTerm}" {t('medications.modal.not_in_catalog')}</p>
                      <p className="text-[10px] font-bold uppercase tracking-widest">{t('medications.modal.add_custom')}</p>
                    </div>
                  </button>
                </div>
              )}

              {searchTerm.length > 2 && catalogResults.length === 0 && !searching && !isAddingNew && !selectedCatalogItem && (
                <button
                  type="button"
                  onClick={() => setIsAddingNew(true)}
                  className="w-full text-left px-6 py-5 bg-blue-50/50 dark:bg-blue-900/10 hover:bg-blue-50 dark:hover:bg-blue-900/20 flex items-center space-x-3 text-blue-600 rounded-2xl border border-dashed border-blue-200"
                >
                  <div className="p-2 bg-blue-600 text-white rounded-xl">
                    <Plus className="w-4 h-4" />
                  </div>
                  <div>
                    <p className="text-sm font-bold italic">"{searchTerm}" {t('medications.modal.not_found')}</p>
                    <p className="text-[10px] font-bold uppercase tracking-widest">{t('medications.modal.add_custom')}</p>
                  </div>
                </button>
              )}

              {isAddingNew && (
                <div className="p-6 bg-blue-50/30 dark:bg-blue-900/10 rounded-2xl border border-blue-100/50 dark:border-blue-900/30 space-y-4 animate-in zoom-in-95">
                  <div className="flex items-center justify-between">
                    <h4 className="text-[10px] font-bold text-blue-600 uppercase tracking-widest">{t('medications.modal.define_new')}</h4>
                    <AIAssistButton 
                      taskType="define_medication"
                      context={{}}
                      placeholder={t('medications.modal.define_new')}
                      onSuggestedData={(data) => {
                        setFormData(prev => ({
                          ...prev,
                          indications: data.indications || prev.indications,
                          side_effects: data.side_effects && Array.isArray(data.side_effects) 
                            ? [...new Set([...prev.side_effects, ...data.side_effects])] 
                            : prev.side_effects
                        }));
                        if (data.name && !searchTerm) setSearchTerm(data.name);
                      }}
                    />
                  </div>
                  
                  <div className="space-y-4">
                    <div>
                      <label className="block text-[10px] font-bold text-gray-400 uppercase mb-1.5 ml-1">{t('medications.modal.main_indications')}</label>
                      <input
                        type="text"
                        placeholder={t('medications.modal.indications_q')}
                        className="w-full px-4 py-3 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-sm focus:ring-2 focus:ring-blue-500 outline-none dark:text-dark-text"
                        value={formData.indications}
                        onChange={e => setFormData({ ...formData, indications: e.target.value })}
                      />
                    </div>
                    
                    <div>
                      <label className="block text-[10px] font-bold text-gray-400 uppercase mb-1.5 ml-1">{t('medications.modal.side_effects')}</label>
                      <div className="flex gap-2 mb-2">
                        <input
                          type="text"
                          placeholder={t('medications.modal.add_side_effect')}
                          className="flex-1 px-4 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-sm focus:ring-2 focus:ring-blue-500 outline-none dark:text-dark-text"
                          onKeyDown={e => {
                            if (e.key === 'Enter') {
                              e.preventDefault();
                              addSideEffect((e.target as HTMLInputElement).value);
                              (e.target as HTMLInputElement).value = '';
                            }
                          }}
                        />
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {formData.side_effects.map((se, i) => (
                          <span key={i} className="px-2 py-1 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-lg text-[10px] font-bold flex items-center space-x-1">
                            <span>{se}</span>
                            <button type="button" onClick={() => removeSideEffect(i)} className="text-gray-400 hover:text-red-500">
                              <X className="w-3 h-3" />
                            </button>
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {selectedCatalogItem && (
                <div className="p-4 bg-blue-50 dark:bg-blue-900/10 border border-blue-100 dark:border-blue-900/30 rounded-2xl animate-in zoom-in-95 duration-200">
                  <div className="flex items-start justify-between">
                    <div>
                      <h4 className="font-bold text-blue-900 dark:text-blue-300">{selectedCatalogItem.name}</h4>
                      <p className="text-xs text-blue-700 dark:text-blue-400/80 mt-1">{selectedCatalogItem.indications}</p>
                    </div>
                    <button 
                      type="button" 
                      onClick={() => setSelectedCatalogItem(null)}
                      className="text-[10px] font-bold text-blue-600 dark:text-blue-400 uppercase tracking-widest hover:underline"
                    >
                      {t('medications.modal.change')}
                    </button>
                  </div>
                  {selectedCatalogItem.side_effects && selectedCatalogItem.side_effects.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-1">
                      {selectedCatalogItem.side_effects.slice(0, 3).map((se, i) => (
                        <span key={i} className="px-2 py-0.5 bg-white/50 dark:bg-dark-surface/50 rounded text-[9px] text-blue-600 dark:text-blue-400 font-bold uppercase tracking-tighter">
                          {se}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}
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
                        className="w-16 px-3 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-lg text-sm font-bold focus:ring-2 focus:ring-blue-500 outline-none dark:text-dark-text"
                        value={timing.period}
                        onChange={e => setTiming({...timing, period: parseInt(e.target.value) || 1})}
                      />
                      <select 
                        className="flex-1 px-3 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-lg text-sm font-bold focus:ring-2 focus:ring-blue-500 outline-none dark:text-dark-text"
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
                      className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                      checked={timing.as_needed}
                      onChange={e => setTiming({...timing, as_needed: e.target.checked})}
                    />
                    <label htmlFor="as_needed" className="text-xs font-bold text-gray-600 dark:text-dark-text cursor-pointer">{t('medications.modal.as_needed_label')}</label>
                  </div>
                </div>

                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1">{t('medications.modal.scheduled_times')}</label>
                    <button type="button" onClick={addTime} className="text-[10px] font-bold text-blue-600 uppercase hover:underline flex items-center">
                      <Plus className="w-3 h-3 mr-1" /> {t('medications.modal.add_time')}
                    </button>
                  </div>
                  <div className="space-y-2 max-h-32 overflow-y-auto pr-2 custom-scrollbar">
                    {timing.time_of_day?.map((t_val, i) => (
                      <div key={i} className="flex items-center space-x-2">
                        <div className="relative flex-1">
                          <Clock className="absolute left-3 top-2.5 w-3.5 h-3.5 text-gray-400" />
                          <input 
                            type="time" 
                            className="w-full pl-9 pr-3 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-lg text-sm font-medium focus:ring-2 focus:ring-blue-500 outline-none dark:text-dark-text"
                            value={t_val}
                            onChange={e => updateTime(i, e.target.value)}
                          />
                        </div>
                        <button type="button" onClick={() => removeTime(i)} className="p-2 text-gray-300 hover:text-red-500">
                          <X className="w-4 h-4" />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
             </div>
          </div>

          {/* Form Fields */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pt-4">
            <div className="space-y-4">
              <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1">{t('medications.modal.current_status')}</label>
              <select
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 outline-none"
                value={formData.status}
                onChange={e => setFormData({...formData, status: e.target.value as any})}
              >
                <option value="active">{t('medications.modal.status.active')}</option>
                <option value="completed">{t('medications.modal.status.completed')}</option>
                <option value="on-hold">{t('medications.modal.status.on_hold')}</option>
                <option value="stopped">{t('medications.modal.status.stopped')}</option>
                <option value="intended">{t('medications.modal.status.intended')}</option>
              </select>
            </div>

            <div className="space-y-4">
              <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1">{t('medications.modal.dosage')}</label>
              <input
                type="text"
                placeholder={t('medications.modal.dosage_placeholder')}
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 outline-none"
                value={formData.dosage}
                onChange={e => setFormData({...formData, dosage: e.target.value})}
              />
            </div>

            <div className="space-y-4">
              <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1">{t('medications.indications')}</label>
              <input
                type="text"
                placeholder={t('medications.modal.reason_placeholder')}
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 outline-none"
                value={formData.reason}
                onChange={e => setFormData({...formData, reason: e.target.value})}
              />
            </div>

            <div className="space-y-2">
              <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1 flex items-center">
                <Calendar className="w-3 h-3 mr-2" />
                {t('medications.modal.start_date')}
              </label>
              <DatePicker
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
              className="w-full px-4 py-4 bg-gray-50 dark:bg-dark-bg border-none rounded-2xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 outline-none resize-none"
              placeholder={t('medications.modal.notes_placeholder')}
              value={formData.note}
              onChange={e => setFormData({...formData, note: e.target.value})}
            />
          </div>
        </form>

        {/* Footer */}
        {showActions && (
          <div className="px-8 py-6 bg-gray-50 dark:bg-dark-bg/50 border-t border-gray-50 dark:border-dark-border flex items-center shrink-0">
            {onReject && (
              <button
                type="button"
                onClick={onReject}
                disabled={loading}
                className="px-5 py-2.5 text-sm font-bold text-rose-600 hover:text-rose-700 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-900/20 rounded-xl transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {rejectLabel ?? t('ai_chat.hitl.reject', 'Reject')}
              </button>
            )}
            <div className="ml-auto flex items-center space-x-4">
              {onCancel && (
                <button
                  type="button"
                  onClick={onCancel}
                  disabled={loading}
                  className="px-6 py-2.5 text-sm font-bold text-gray-500 hover:text-gray-700 dark:text-dark-muted transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {t('common.cancel')}
                </button>
              )}
              <button
                onClick={handleSubmit}
                disabled={loading || (!selectedCatalogItem && !searchTerm && !medication)}
                className="px-8 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-xl font-bold text-sm shadow-lg shadow-blue-500/20 transition-all flex items-center space-x-2"
              >
                {loading ? (
                  <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent" />
                ) : (
                  <Save className="w-4 h-4" />
                )}
                <span>{submitLabel ?? (medication ? t('medications.modal.update_record') : t('medications.modal.save_medication'))}</span>
              </button>
            </div>
          </div>
        )}
      </div>
    );
  }
);
