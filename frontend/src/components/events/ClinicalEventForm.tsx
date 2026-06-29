import React, { useState, useEffect, useMemo, forwardRef, useImperativeHandle } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Info, Plus, Activity, Baby, AlertTriangle, Zap, Scissors, Smile, Eye, Sparkles, CheckCircle, Stethoscope, Filter, Calendar as CalendarIcon, Clock, ChevronDown, Save, X, Network
} from 'lucide-react';
import {
  getEventTypes,
  getEventCategories,
  ClinicalEventType,
  ClinicalEventCategory,
  ClinicalEvent,
  ClinicalEventStatus,
} from '../../services/clinicalEventService';
import { getExaminations } from '../../services/examinationService';
import { listObservations } from '../../services/observationService';
import { DynamicMetadataForm } from './DynamicMetadataForm';
import { ExaminationSelectorModal } from '../examinations/ExaminationSelectorModal';
import { ObservationSelectorModal } from '../observations/ObservationSelectorModal';
import { AnatomySearchPopup } from '../anatomy/AnatomySearchPopup';
import { DatePicker } from '../ui/DatePicker';

export type ClinicalEventCodingSystem = 'loinc' | 'snomed' | 'custom';

export interface ClinicalEventFormPayload {
  title: string;
  description: string;
  status: ClinicalEventStatus;
  onset_date: string | null;
  resolved_date: string | null;
  event_metadata: Record<string, any>;
  occurrences: any[];
  examinations: { examination_id: string; reason: string }[];
  observations: { observation_id: string; notes: string }[];
  coding_system: ClinicalEventCodingSystem;
  code: string;
  patient_id: string;
  type_id: string;
}

export interface ClinicalEventFormPrefill {
  type_id?: string;
  type_slug?: string;
  title?: string;
  description?: string;
  status?: string;
  onset_date?: string;
  resolved_date?: string;
  event_metadata?: Record<string, any>;
  occurrences?: any[];
  coding_system?: ClinicalEventCodingSystem;
  code?: string;
}

export interface ClinicalEventFormHandle {
  submit: () => void;
}

interface ClinicalEventFormProps {
  patientId: string;
  event?: ClinicalEvent;
  prefill?: ClinicalEventFormPrefill;
  onSubmit: (payload: ClinicalEventFormPayload) => Promise<void>;
  onCancel?: () => void;
  /** Explicit rejection (e.g. dismiss an AI proposal). When provided, the
   * footer renders a third, danger-toned "Reject" button. Differs from Cancel
   * (which just closes the surface with no state change). */
  onReject?: () => void;
  submitLabel?: string;
  rejectLabel?: string;
  isEdit?: boolean;
  showHeader?: boolean;
  showActions?: boolean;
}

const DEFAULT_RECURRENCE = {
  is_recurring: false,
  frequency: 'daily',
  interval: 1,
  days_of_week: [] as string[],
  time_of_day: '12:00',
};

const buildInitialFormData = () => ({
  title: '',
  description: '',
  status: ClinicalEventStatus.ACTIVE,
  onset_date: '',
  resolved_date: '',
  event_metadata: { recurrence: { ...DEFAULT_RECURRENCE } } as Record<string, any>,
  occurrences: [] as any[],
  examinations: [] as { examination_id: string; reason: string }[],
  observations: [] as { observation_id: string; notes: string }[],
  coding_system: 'custom' as ClinicalEventCodingSystem,
  code: '',
});

export const ClinicalEventForm = forwardRef<ClinicalEventFormHandle, ClinicalEventFormProps>(
  function ClinicalEventForm(
    {
      patientId,
      event,
      prefill,
      onSubmit,
      onCancel,
      onReject,
      submitLabel,
      rejectLabel,
      isEdit,
      showHeader = true,
      showActions = true,
    },
    ref
  ) {
    const { t } = useTranslation();
    const [loading, setLoading] = useState(false);
    const [types, setTypes] = useState<ClinicalEventType[]>([]);
    const [categories, setCategories] = useState<ClinicalEventCategory[]>([]);
    const [examinations, setExaminations] = useState<any[]>([]);
    const [patientObservations, setPatientObservations] = useState<any[]>([]);
    const [selectedTypeId, setSelectedTypeId] = useState<string>('');
    const [activeCategoryId, setActiveCategoryId] = useState<string>('All');
    const [isSelectorOpen, setIsSelectorOpen] = useState(false);
    const [isObsSelectorOpen, setIsObsSelectorOpen] = useState(false);

    const [formData, setFormData] = useState(buildInitialFormData);

    const editMode = isEdit ?? !!event;

    // Resolve which type id to preselect once types are loaded
    const resolveInitialTypeId = (typesData: ClinicalEventType[]): string => {
      if (event?.type_id) return event.type_id;
      if (prefill?.type_id) return prefill.type_id;
      if (prefill?.type_slug) {
        const matched = typesData.find(ty => ty.slug === prefill.type_slug);
        if (matched) return matched.id;
      }
      return '';
    };

    useEffect(() => {
      const loadInitialData = async () => {
        try {
          const [typesData, categoriesData, examsData, obsData] = await Promise.all([
            getEventTypes(),
            getEventCategories(),
            getExaminations(patientId),
            listObservations(undefined, patientId),
          ]);
          setTypes(typesData);
          setCategories(categoriesData);
          setExaminations(examsData || []);
          setPatientObservations(obsData?.items || []);

          if (event) {
            setFormData({
              title: event.title,
              description: event.description || '',
              status: event.status,
              onset_date: event.onset_date ? event.onset_date.split('T')[0] : '',
              resolved_date: event.resolved_date ? event.resolved_date.split('T')[0] : '',
              event_metadata: {
                recurrence: { ...DEFAULT_RECURRENCE },
                ...(event.event_metadata || {}),
              },
              occurrences: event.occurrences || [],
              examinations:
                event.examinations?.map(e => ({
                  examination_id: e.examination_id,
                  reason: e.reason || '',
                })) || [],
              observations:
                event.observations?.map(o => ({
                  observation_id: o.observation_id || o.id,
                  notes: o.notes || '',
                })) || [],
              coding_system: (event.coding_system as ClinicalEventCodingSystem) || 'custom',
              code: event.code || '',
            });
            const typeId = resolveInitialTypeId(typesData);
            setSelectedTypeId(typeId);
            const type = typesData.find(ty => ty.id === typeId);
            if (type && type.category_id) setActiveCategoryId(type.category_id);

          } else if (prefill) {
            const base = buildInitialFormData();
            setFormData({
              ...base,
              title: prefill.title ?? '',
              description: prefill.description ?? '',
              status: (prefill.status as ClinicalEventStatus) || ClinicalEventStatus.ACTIVE,
              onset_date: prefill.onset_date ? prefill.onset_date.split('T')[0] : '',
              resolved_date: prefill.resolved_date ? prefill.resolved_date.split('T')[0] : '',
              event_metadata: {
                recurrence: { ...DEFAULT_RECURRENCE },
                ...(prefill.event_metadata || {}),
              },
              occurrences: prefill.occurrences || [],
              coding_system: prefill.coding_system || 'custom',
              code: prefill.code || '',
            });
            const typeId = resolveInitialTypeId(typesData);
            setSelectedTypeId(typeId);
            const type = typesData.find(ty => ty.id === typeId);
            if (type && type.category_id) setActiveCategoryId(type.category_id);
          } else {
            setSelectedTypeId('');
            setActiveCategoryId('All');
            setFormData(buildInitialFormData());
          }
        } catch (err) {
          console.error('Failed to load event form data', err);
        }
      };

      loadInitialData();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [patientId, event, prefill]);

    const selectedType = types.find(t => t.id === selectedTypeId);

    const filteredTypes = useMemo(() => {
      if (activeCategoryId === 'All') return types;
      return types.filter(t => t.category_id === activeCategoryId);
    }, [types, activeCategoryId]);

    const handleSubmit = async (e?: React.FormEvent) => {
      if (e) e.preventDefault();
      if (loading || !formData.title || !selectedTypeId) return;
      setLoading(true);
      try {
        const payload: ClinicalEventFormPayload = {
          ...formData,
          patient_id: patientId,
          type_id: selectedTypeId,
          onset_date: formData.onset_date ? new Date(formData.onset_date).toISOString() : null,
          resolved_date: formData.resolved_date ? new Date(formData.resolved_date).toISOString() : null,
        };
        await onSubmit(payload);
      } catch (err) {
        console.error('ClinicalEventForm submit failed', err);
      } finally {
        setLoading(false);
      }
    };

    // Expose submit via ref so parent surfaces (e.g. HITL card) can trigger it.
    // No dependency array: recreated each render — handleSubmit closes over
    // fresh form state, and the ref is cheap to reassign.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    useImperativeHandle(ref, () => ({ submit: () => handleSubmit() }));

    const toggleExamination = (examId: string) => {
      setFormData({
        ...formData,
        examinations: formData.examinations.filter(e => e.examination_id !== examId),
      });
    };

    const updateExamReason = (examId: string, reason: string) => {
      setFormData({
        ...formData,
        examinations: formData.examinations.map(e =>
          e.examination_id === examId ? { ...e, reason } : e
        ),
      });
    };

    const handleSelectionChange = (newIds: string[]) => {
      const current = formData.examinations;
      const updated = newIds.map(id => {
        const existing = current.find(e => e.examination_id === id);
        return existing || { examination_id: id, reason: '' };
      });
      setFormData({ ...formData, examinations: updated });
    };

    const toggleObservation = (obsId: string) => {
      setFormData({
        ...formData,
        observations: formData.observations.filter(o => o.observation_id !== obsId),
      });
    };

    const updateObservationNotes = (obsId: string, notes: string) => {
      setFormData({
        ...formData,
        observations: formData.observations.map(o =>
          o.observation_id === obsId ? { ...o, notes } : o
        ),
      });
    };

    const handleObsSelectionChange = (newIds: string[]) => {
      const current = formData.observations;
      const updated = newIds.map(id => {
        const existing = current.find(o => o.observation_id === id);
        return existing || { observation_id: id, notes: '' };
      });
      setFormData({ ...formData, observations: updated });
    };

    const getIcon = (slug: string) => {
      switch (slug) {
        case 'pain-episode': return <Activity className="w-5 h-5" />;
        case 'pregnancy': return <Baby className="w-5 h-5" />;
        case 'accident': return <AlertTriangle className="w-5 h-5" />;
        case 'flare-up': return <Zap className="w-5 h-5" />;
        case 'surgical-recovery': return <Scissors className="w-5 h-5" />;
        case 'dental': return <Smile className="w-5 h-5" />;
        case 'vision': return <Eye className="w-5 h-5" />;
        case 'aesthetic': return <Sparkles className="w-5 h-5" />;
        case 'maintenance': return <CheckCircle className="w-5 h-5" />;
        case 'reproductive-health': return <Baby className="w-4 h-4" />;
        case 'acute-chronic': return <Activity className="w-4 h-4" />;
        case 'specialized-care': return <Stethoscope className="w-4 h-4" />;
        case 'routine-wellness': return <CheckCircle className="w-4 h-4" />;
        default: return <Activity className="w-5 h-5" />;
      }
    };

    const fieldInput =
      'w-full px-5 py-4 bg-gray-50 dark:bg-dark-bg border border-transparent rounded-2xl text-gray-900 dark:text-dark-text focus:ring-4 focus:ring-blue-500/10 focus:bg-white dark:focus:bg-dark-surface outline-none font-bold transition-all';

    return (
      <div className="flex flex-col flex-1 min-h-0">
        {/* Header */}
        {showHeader && (
          <div className="px-8 py-6 border-b border-gray-50 dark:border-dark-border flex items-center justify-between bg-white dark:bg-dark-surface shrink-0">
            <div className="flex items-center space-x-4">
              <div
                className="p-3 rounded-2xl bg-opacity-10 shadow-sm transition-all duration-500"
                style={{ backgroundColor: selectedType?.color ? selectedType.color + '20' : '#3b82f620' }}
              >
                <div style={{ color: selectedType?.color || '#3b82f6' }}>
                  {getIcon(selectedType?.slug || 'default')}
                </div>
              </div>
              <div>
                <h2 className="text-xl font-bold text-gray-900 dark:text-dark-text tracking-tight">
                  {editMode ? t('events.update_title') : t('events.new_title')}
                </h2>
                <div className="flex items-center mt-1 space-x-2">
                  <span className="text-[10px] text-gray-500 dark:text-dark-muted font-bold uppercase tracking-widest">
                    {selectedType?.name || t('events.select_type')}
                  </span>
                  {selectedType?.category && (
                    <>
                      <span className="w-1 h-1 rounded-full bg-gray-300" />
                      <span className="text-[10px] text-blue-500 font-black uppercase tracking-widest">
                        {selectedType.category.name}
                      </span>
                    </>
                  )}
                </div>
              </div>
            </div>
            {onCancel && (
              <button
                type="button"
                onClick={onCancel}
                className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors"
                aria-label={t('common.cancel', 'Close')}
              >
                <X className="w-5 h-5 text-gray-400" />
              </button>
            )}
          </div>
        )}

        <form onSubmit={handleSubmit} className="flex-1 min-h-0 overflow-y-auto p-8 space-y-10 custom-scrollbar">
          {/* Event Type Selection (only for new events) */}
          {!editMode && (
            <div className="space-y-6">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2">
                  <Filter className="w-3.5 h-3.5 text-blue-500" />
                  <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-gray-400 dark:text-dark-muted">
                    {t('events.filter_by_specialty')}
                  </h3>
                </div>
              </div>

              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => setActiveCategoryId('All')}
                  className={`px-4 py-2 text-[10px] font-bold uppercase tracking-widest rounded-xl transition-all ${
                    activeCategoryId === 'All'
                      ? 'bg-blue-600 text-white shadow-lg shadow-blue-500/20'
                      : 'bg-gray-50 dark:bg-dark-bg text-gray-500 hover:bg-gray-100'
                  }`}
                >
                  {t('common.view_all')}
                </button>
                {categories.map(cat => (
                  <button
                    key={cat.id}
                    type="button"
                    onClick={() => setActiveCategoryId(cat.id)}
                    className={`flex items-center space-x-2 px-4 py-2 text-[10px] font-bold uppercase tracking-widest rounded-xl transition-all ${
                      activeCategoryId === cat.id
                        ? 'bg-blue-600 text-white shadow-lg shadow-blue-500/20'
                        : 'bg-gray-50 dark:bg-dark-bg text-gray-500 hover:bg-gray-100'
                    }`}
                  >
                    {getIcon(cat.slug)}
                    <span>{cat.name}</span>
                  </button>
                ))}
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
                {filteredTypes.map(type => (
                  <button
                    key={type.id}
                    type="button"
                    onClick={() => {
                      setSelectedTypeId(type.id);
                      setFormData(prev => ({
                        ...prev,
                        event_metadata: { recurrence: { ...DEFAULT_RECURRENCE } },
                      }));
                    }}
                    className={`flex flex-col items-center justify-center p-5 rounded-3xl border transition-all duration-300 space-y-3 relative group ${
                      selectedTypeId === type.id
                        ? 'bg-white dark:bg-dark-surface border-blue-500 shadow-xl shadow-blue-500/10 ring-2 ring-blue-500/20 scale-[1.05] z-10'
                        : 'bg-gray-50 dark:bg-dark-bg border-transparent hover:border-blue-200 hover:bg-white dark:hover:bg-dark-surface'
                    }`}
                  >
                    <div
                      className={`p-3 rounded-2xl transition-all duration-300 ${selectedTypeId === type.id ? 'bg-blue-600 text-white' : 'bg-white dark:bg-dark-surface shadow-sm text-gray-400 group-hover:text-blue-500'}`}
                      style={selectedTypeId !== type.id ? { color: type.color } : undefined}
                    >
                      {getIcon(type.slug)}
                    </div>
                    <span className={`text-[10px] font-black uppercase tracking-tight text-center ${selectedTypeId === type.id ? 'text-blue-600 dark:text-blue-400' : 'text-gray-500 dark:text-dark-muted'}`}>
                      {type.name}
                    </span>
                    {selectedTypeId === type.id && (
                      <div className="absolute top-2 right-2">
                        <CheckCircle className="w-4 h-4 text-blue-500" />
                      </div>
                    )}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-10">
            <div className="space-y-8">
              <div className="space-y-1">
                <label className="block text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em] ml-1">
                  {t('events.event_title')} *
                </label>
                <input
                  type="text"
                  required
                  placeholder={selectedType ? t(`events.placeholders.${selectedType.slug}.title`) : t('events.title_placeholder')}
                  className={fieldInput}
                  value={formData.title}
                  onChange={e => setFormData({ ...formData, title: e.target.value })}
                />
              </div>

              <div className="space-y-1">
                <label className="block text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em] ml-1">
                  {t('events.description')}
                </label>
                <textarea
                  rows={3}
                  placeholder={selectedType ? t(`events.placeholders.${selectedType.slug}.description`) : t('events.description_placeholder')}
                  className="w-full px-5 py-4 bg-gray-50 dark:bg-dark-bg border border-transparent rounded-2xl text-gray-900 dark:text-dark-text focus:ring-4 focus:ring-blue-500/10 focus:bg-white dark:focus:bg-dark-surface outline-none resize-none transition-all font-medium"
                  value={formData.description}
                  onChange={e => setFormData({ ...formData, description: e.target.value })}
                />
              </div>

              <div className="grid grid-cols-2 gap-6">
                <div className="space-y-1">
                  <label className="block text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em] ml-1 mb-2">{t('events.onset_date')}</label>
                  <DatePicker
                    value={formData.onset_date}
                    onChange={date => setFormData({ ...formData, onset_date: date })}
                  />
                </div>
                <div className="space-y-1">
                  <label className="block text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em] ml-1 mb-2">{t('events.resolved_date')}</label>
                  <DatePicker
                    value={formData.resolved_date}
                    onChange={date => setFormData({ ...formData, resolved_date: date })}
                  />
                </div>
              </div>
            </div>

            <div className="space-y-8">
              <div className="space-y-2">
                <label className="block text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em] ml-1">{t('events.current_status')}</label>
                <div className="grid grid-cols-2 gap-3">
                  {[ClinicalEventStatus.ACTIVE, ClinicalEventStatus.RESOLVED, ClinicalEventStatus.ON_HOLD, ClinicalEventStatus.UNKNOWN].map(s => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => setFormData({ ...formData, status: s })}
                      className={`px-4 py-3 rounded-2xl text-xs font-black transition-all border ${
                        formData.status === s
                          ? 'bg-blue-600 border-blue-600 text-white shadow-xl shadow-blue-500/20'
                          : 'bg-white dark:bg-dark-bg border-gray-100 dark:border-dark-border text-gray-400 hover:border-blue-200 hover:text-blue-500'
                      }`}
                    >
                      {t(`events.status.${s.toLowerCase()}`)}
                    </button>
                  ))}
                </div>
              </div>

              {/* Related Examinations */}
              <div className="space-y-4">
                <div className="flex items-center justify-between border-b border-gray-50 dark:border-dark-border pb-2">
                  <div className="flex items-center space-x-2">
                    <Stethoscope className="w-4 h-4 text-indigo-500" />
                    <h3 className="text-sm font-bold text-gray-900 dark:text-dark-text tracking-tight">{t('events.related_visits')}</h3>
                  </div>
                  <button
                    type="button"
                    onClick={() => setIsSelectorOpen(true)}
                    className="flex items-center space-x-1 px-3 py-1.5 bg-indigo-50 dark:bg-indigo-900/20 text-indigo-600 dark:text-indigo-400 rounded-lg hover:bg-indigo-100 transition-all text-[10px] font-bold uppercase shadow-sm"
                  >
                    <Plus className="w-3 h-3" />
                    <span>{t('common.manage')}</span>
                  </button>
                </div>

                <div className="max-h-[350px] overflow-y-auto pr-4 custom-scrollbar space-y-3">
                  {formData.examinations.length === 0 ? (
                    <div className="p-8 text-center bg-gray-50 dark:bg-dark-bg/50 rounded-2xl border border-dashed border-gray-200 dark:border-dark-border">
                      <p className="text-xs text-gray-400 italic">{t('events.no_visits_linked')}</p>
                      <button
                        type="button"
                        onClick={() => setIsSelectorOpen(true)}
                        className="mt-2 text-[10px] font-black text-indigo-600 uppercase hover:underline"
                      >
                        {t('events.click_to_link')}
                      </button>
                    </div>
                  ) : (
                    formData.examinations.map(link => {
                      const exam = examinations.find(e => e.id === link.examination_id);
                      if (!exam) return null;
                      return (
                        <div
                          key={exam.id}
                          className="bg-white dark:bg-dark-surface p-4 rounded-2xl border border-indigo-100 dark:border-indigo-900/30 shadow-sm animate-in slide-in-from-right-2"
                        >
                          <div className="flex items-center justify-between mb-3">
                            <div className="flex items-center space-x-3">
                              <div className="w-2 h-2 rounded-full bg-indigo-500 shadow-[0_0_8px_rgba(99,102,241,0.5)]" />
                              <span className="text-xs font-black text-gray-800 dark:text-dark-text">
                                {new Date(exam.examination_date).toLocaleDateString()}
                              </span>
                              <span className="px-2 py-0.5 bg-indigo-50 dark:bg-indigo-900/20 text-[9px] font-bold text-indigo-600 dark:text-indigo-400 rounded-md uppercase border border-indigo-100/50 dark:border-indigo-800/30">
                                {exam.category || 'General'}
                              </span>
                            </div>
                            <button
                              type="button"
                              onClick={() => toggleExamination(exam.id)}
                              className="p-1.5 text-gray-300 hover:text-red-500 transition-colors"
                            >
                              <X className="w-3.5 h-3.5" />
                            </button>
                          </div>
                          <input
                            type="text"
                            placeholder={t('events.reason_for_visit_placeholder')}
                            className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-transparent rounded-xl text-[10px] font-bold focus:ring-4 focus:ring-indigo-500/10 outline-none transition-all placeholder:font-medium dark:text-dark-text"
                            value={link.reason || ''}
                            onChange={e => updateExamReason(exam.id, e.target.value)}
                          />
                        </div>
                      );
                    })
                  )}
                </div>
              </div>

              {/* Related Biomarkers */}
              <div className="space-y-4">
                <div className="flex items-center justify-between border-b border-gray-50 dark:border-dark-border pb-2">
                  <div className="flex items-center space-x-2">
                    <Activity className="w-4 h-4 text-blue-500" />
                    <h3 className="text-sm font-bold text-gray-900 dark:text-dark-text tracking-tight">{t('events.related_biomarkers')}</h3>
                  </div>
                  <button
                    type="button"
                    onClick={() => setIsObsSelectorOpen(true)}
                    className="flex items-center space-x-1 px-3 py-1.5 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-lg hover:bg-blue-100 transition-all text-[10px] font-bold uppercase shadow-sm"
                  >
                    <Plus className="w-3 h-3" />
                    <span>{t('common.manage')}</span>
                  </button>
                </div>

                <div className="max-h-[350px] overflow-y-auto pr-4 custom-scrollbar space-y-3">
                  {formData.observations.length === 0 ? (
                    <div className="p-8 text-center bg-gray-50 dark:bg-dark-bg/50 rounded-2xl border border-dashed border-gray-200 dark:border-dark-border">
                      <p className="text-xs text-gray-400 italic">{t('events.no_biomarkers_linked')}</p>
                      <button
                        type="button"
                        onClick={() => setIsObsSelectorOpen(true)}
                        className="mt-2 text-[10px] font-black text-blue-600 uppercase hover:underline"
                      >
                        {t('events.click_to_link_biomarkers')}
                      </button>
                    </div>
                  ) : (
                    formData.observations.map(link => {
                      const obs = patientObservations.find(o => o.id === link.observation_id);
                      if (!obs) return null;
                      return (
                        <div
                          key={obs.id}
                          className="bg-white dark:bg-dark-surface p-4 rounded-2xl border border-blue-100 dark:border-blue-900/30 shadow-sm animate-in slide-in-from-right-2"
                        >
                          <div className="flex items-center justify-between mb-3">
                            <div className="flex items-center space-x-3">
                              <div className="w-2 h-2 rounded-full bg-blue-500 shadow-[0_0_8px_rgba(59,130,246,0.5)]" />
                              <span className="text-xs font-black text-gray-800 dark:text-dark-text">
                                {obs.code?.text || obs.code?.coding?.[0]?.display || obs.biomarker_slug || 'Unknown'}
                              </span>
                              <span className="px-2 py-0.5 bg-blue-50 dark:bg-blue-900/20 text-[9px] font-bold text-blue-600 dark:text-blue-400 rounded-md uppercase border border-blue-100/50 dark:border-blue-800/30">
                                {obs.raw_value} {obs.normalized_unit}
                              </span>
                            </div>
                            <button
                              type="button"
                              onClick={() => toggleObservation(obs.id)}
                              className="p-1.5 text-gray-300 hover:text-red-500 transition-colors"
                            >
                              <X className="w-3.5 h-3.5" />
                            </button>
                          </div>
                          <input
                            type="text"
                            placeholder={t('events.notes_for_biomarker_placeholder')}
                            className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-transparent rounded-xl text-[10px] font-bold focus:ring-4 focus:ring-blue-500/10 outline-none transition-all placeholder:font-medium dark:text-dark-text"
                            value={link.notes || ''}
                            onChange={e => updateObservationNotes(obs.id, e.target.value)}
                          />
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* Dynamic Metadata Section */}
          {selectedType && selectedType.metadata_schema && (
            <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
              <DynamicMetadataForm
                schema={selectedType.metadata_schema}
                color={selectedType.color}
                value={formData.event_metadata}
                onChange={val => setFormData({ ...formData, event_metadata: { ...formData.event_metadata, ...val } })}
              />
            </div>
          )}

          {/* Schedule & Recurrence Section */}
          <div className="rounded-3xl p-8 border border-gray-100 dark:border-dark-border bg-gray-50/30 dark:bg-dark-bg/20 space-y-8 animate-in slide-in-from-bottom-4 duration-500 delay-75">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <div className="p-2 bg-blue-50 dark:bg-blue-900/20 text-blue-600 rounded-xl">
                  <CalendarIcon className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-sm font-bold text-gray-900 dark:text-dark-text uppercase tracking-widest">{t('events.schedule_recurrence')}</h3>
                  <p className="text-[10px] text-gray-400 font-bold uppercase tracking-tighter mt-0.5">{t('events.schedule_recurrence_desc')}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={() =>
                  setFormData({
                    ...formData,
                    event_metadata: {
                      ...formData.event_metadata,
                      recurrence: {
                        ...formData.event_metadata?.recurrence,
                        is_recurring: !formData.event_metadata?.recurrence?.is_recurring,
                      },
                    },
                  })
                }
                className={`px-4 py-2 rounded-xl text-xs font-black transition-all ${
                  formData.event_metadata?.recurrence?.is_recurring
                    ? 'bg-blue-600 text-white shadow-lg shadow-blue-500/20'
                    : 'bg-white dark:bg-dark-bg text-gray-400 border border-gray-200 dark:border-dark-border'
                }`}
              >
                {formData.event_metadata?.recurrence?.is_recurring ? t('events.recurring_enabled') : t('events.enable_recurrence')}
              </button>
            </div>

            {formData.event_metadata?.recurrence?.is_recurring && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-8 pt-4 animate-in fade-in zoom-in-95 duration-300">
                <div className="space-y-4">
                  <label className="block text-[10px] font-black text-gray-400 uppercase tracking-widest ml-1">{t('events.frequency')}</label>
                  <div className="grid grid-cols-3 gap-2">
                    {['daily', 'weekly', 'monthly'].map(f => (
                      <button
                        key={f}
                        type="button"
                        onClick={() =>
                          setFormData({
                            ...formData,
                            event_metadata: {
                              ...formData.event_metadata,
                              recurrence: { ...formData.event_metadata?.recurrence, frequency: f },
                            },
                          })
                        }
                        className={`py-2.5 rounded-xl text-[10px] font-black uppercase transition-all border ${
                          formData.event_metadata?.recurrence?.frequency === f
                            ? 'bg-blue-50 border-blue-200 text-blue-600 shadow-sm'
                            : 'bg-white dark:bg-dark-bg border-gray-100 dark:border-dark-border text-gray-400'
                        }`}
                      >
                        {t(`events.freq.${f}`)}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="space-y-4">
                  <label className="block text-[10px] font-black text-gray-400 uppercase tracking-widest ml-1">
                    {t('events.repeat_every')}
                  </label>
                  <div className="flex items-center space-x-3">
                    <input
                      type="number"
                      min="1"
                      className="w-20 px-4 py-2.5 bg-white dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl text-sm font-black outline-none focus:ring-2 focus:ring-blue-500/20"
                      value={formData.event_metadata?.recurrence?.interval}
                      onChange={e =>
                        setFormData({
                          ...formData,
                          event_metadata: {
                            ...formData.event_metadata,
                            recurrence: { ...formData.event_metadata?.recurrence, interval: parseInt(e.target.value) || 1 },
                          },
                        })
                      }
                    />
                    <span className="text-xs font-bold text-gray-500 dark:text-dark-muted lowercase">
                      {formData.event_metadata?.recurrence?.frequency === 'daily' ? t('events.days') : formData.event_metadata?.recurrence?.frequency === 'weekly' ? t('events.weeks') : t('events.months')}
                    </span>
                  </div>
                </div>

                {formData.event_metadata?.recurrence?.frequency === 'weekly' && (
                  <div className="md:col-span-2 space-y-4">
                    <label className="block text-[10px] font-black text-gray-400 uppercase tracking-widest ml-1">{t('events.on_days')}</label>
                    <div className="flex flex-wrap gap-2">
                      {['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'].map(day => {
                        const isSelected = formData.event_metadata?.recurrence?.days_of_week?.includes(day);
                        return (
                          <button
                            key={day}
                            type="button"
                            onClick={() => {
                              const current = formData.event_metadata?.recurrence?.days_of_week || [];
                              const updated = isSelected ? current.filter((d: string) => d !== day) : [...current, day];
                              setFormData({
                                ...formData,
                                event_metadata: {
                                  ...formData.event_metadata,
                                  recurrence: { ...formData.event_metadata?.recurrence, days_of_week: updated },
                                },
                              });
                            }}
                            className={`w-10 h-10 rounded-xl text-[10px] font-black uppercase transition-all border flex items-center justify-center ${
                              isSelected
                                ? 'bg-blue-600 border-blue-600 text-white shadow-md'
                                : 'bg-white dark:bg-dark-bg border-gray-100 dark:border-dark-border text-gray-400'
                            }`}
                          >
                            {day.charAt(0)}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}

                <div className="space-y-4">
                  <label className="block text-[10px] font-black text-gray-400 uppercase tracking-widest ml-1 flex items-center">
                    <Clock className="w-3 h-3 mr-1.5" />
                    {t('events.preferred_time')}
                  </label>
                  <input
                    type="time"
                    className="w-full px-4 py-2.5 bg-white dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl text-sm font-black outline-none focus:ring-2 focus:ring-blue-500/20"
                    value={formData.event_metadata?.recurrence?.time_of_day}
                    onChange={e =>
                      setFormData({
                        ...formData,
                        event_metadata: {
                          ...formData.event_metadata,
                          recurrence: { ...formData.event_metadata?.recurrence, time_of_day: e.target.value },
                        },
                      })
                    }
                  />
                </div>
              </div>
            )}
          </div>

          {/* Recurring Occurrences (Pain, Flare-ups, etc.) */}
          {(selectedType?.slug === 'pain-episode' || selectedType?.slug === 'flare-up') && (
            <div className="space-y-6">
              <div className="flex items-center justify-between border-b border-gray-50 dark:border-dark-border pb-2">
                <div className="flex items-center space-x-2">
                  <Activity className="w-4 h-4 text-red-500" />
                  <h3 className="text-sm font-bold text-gray-900 dark:text-dark-text tracking-tight">{t('events.episode_tracking')}</h3>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    const now = new Date();
                    const date = now.toISOString().split('T')[0];
                    const time = now.toTimeString().split(' ')[0].substring(0, 5);
                    const newOccurrence = { date, time, intensity: 5, notes: '', location: '' };
                    setFormData({ ...formData, occurrences: [...formData.occurrences, newOccurrence] });
                  }}
                  className="flex items-center space-x-1 px-3 py-1.5 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-lg hover:bg-blue-100 transition-all text-[10px] font-bold uppercase"
                >
                  <Plus className="w-3 h-3" />
                  <span>{t('events.add_occurrence')}</span>
                </button>
              </div>

              <div className="space-y-4">
                {formData.occurrences.length === 0 ? (
                  <div className="p-8 text-center bg-gray-50 dark:bg-dark-bg/50 rounded-2xl border border-dashed border-gray-200 dark:border-dark-border">
                    <p className="text-xs text-gray-400 italic">{t('events.no_occurrences_hint')}</p>
                  </div>
                ) : (
                  formData.occurrences.map((occ, i) => (
                    <div key={i} className="bg-white dark:bg-dark-surface p-6 rounded-2xl border border-gray-100 dark:border-dark-border shadow-sm flex flex-col space-y-4 animate-in slide-in-from-top-2">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center space-x-3 flex-1">
                          <div className="flex items-center space-x-1">
                            <DatePicker
                              variant="unstyled"
                              className="px-2 py-1.5 bg-gray-50 dark:bg-dark-bg border-none rounded-lg text-[10px] font-bold focus:ring-2 focus:ring-blue-500/20 outline-none w-28 dark:text-dark-text"
                              value={occ.date}
                              onChange={date => {
                                const updated = [...formData.occurrences];
                                updated[i] = { ...updated[i], date: date };
                                setFormData({ ...formData, occurrences: updated });
                              }}
                            />
                            <input
                              type="time"
                              className="px-2 py-1.5 bg-gray-50 dark:bg-dark-bg border-none rounded-lg text-[10px] font-bold focus:ring-2 focus:ring-blue-500/20 outline-none w-20 dark:text-dark-text"
                              value={occ.time || '12:00'}
                              onChange={e => {
                                const updated = [...formData.occurrences];
                                updated[i] = { ...updated[i], time: e.target.value };
                                setFormData({ ...formData, occurrences: updated });
                              }}
                            />
                          </div>
                          <div className="flex-1 flex items-center space-x-4">
                            <div className="flex-1 flex items-center space-x-2">
                              <label className="text-[9px] font-black text-gray-400 uppercase tracking-tighter">{t('events.intensity')}</label>
                              <input
                                type="range"
                                min="1"
                                max="10"
                                className="flex-1 h-1 bg-gray-100 dark:bg-dark-bg rounded-lg appearance-none cursor-pointer accent-red-500"
                                value={occ.intensity}
                                onChange={e => {
                                  const updated = [...formData.occurrences];
                                  updated[i] = { ...updated[i], intensity: parseInt(e.target.value) };
                                  setFormData({ ...formData, occurrences: updated });
                                }}
                              />
                              <span className={`w-5 h-5 rounded flex items-center justify-center text-[9px] font-black text-white ${occ.intensity > 7 ? 'bg-red-500' : occ.intensity > 4 ? 'bg-yellow-500' : 'bg-green-500'}`}>
                                {occ.intensity}
                              </span>
                            </div>
                            <div className="w-1/3">
                              <AnatomySearchPopup
                                selectedId={occ.location}
                                onSelect={(s) => {
                                  const updated = [...formData.occurrences];
                                  updated[i] = { ...updated[i], location: s.id };
                                  setFormData({ ...formData, occurrences: updated });
                                }}
                                placeholder={t('events.location_placeholder')}
                                innerClassName="px-3 py-1.5 text-[10px]"
                              />
                            </div>
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => {
                            setFormData({
                              ...formData,
                              occurrences: formData.occurrences.filter((_, idx) => idx !== i),
                            });
                          }}
                          className="ml-4 p-2 text-gray-300 hover:text-red-500 transition-colors"
                        >
                          <X className="w-4 h-4" />
                        </button>
                      </div>
                      <input
                        type="text"
                        placeholder={t('events.occurrence_notes_placeholder')}
                        className="w-full px-4 py-2 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-xs font-medium focus:ring-2 focus:ring-blue-500/20 outline-none dark:text-dark-text"
                        value={occ.notes || ''}
                        onChange={e => {
                          const updated = [...formData.occurrences];
                          updated[i] = { ...updated[i], notes: e.target.value };
                          setFormData({ ...formData, occurrences: updated });
                        }}
                      />
                    </div>
                  ))
                )}
              </div>
            </div>
          )}

          {/* Clinical Coding Section */}
          <div className="space-y-6">
            <label className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em] px-1 flex items-center">
              <Info className="w-3 h-3 mr-2 text-blue-500" />
              {t('events.internal_coding')} (Optional)
            </label>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
              <div className="space-y-2">
                <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">Coding System</label>
                <div className="relative">
                  <select
                    className="w-full px-5 py-4 bg-gray-50 dark:bg-dark-bg border border-transparent rounded-2xl text-gray-900 dark:text-dark-text focus:ring-4 focus:ring-blue-500/10 focus:bg-white dark:focus:bg-dark-surface outline-none appearance-none font-bold transition-all"
                    value={formData.coding_system}
                    onChange={e => setFormData(prev => ({ ...prev, coding_system: e.target.value as ClinicalEventCodingSystem }))}
                  >
                    <option value="loinc">LOINC</option>
                    <option value="snomed">SNOMED</option>
                    <option value="custom">Custom</option>
                  </select>
                  <div className="absolute inset-y-0 right-0 flex items-center pr-4 pointer-events-none">
                    <ChevronDown className="w-4 h-4 text-gray-400" />
                  </div>
                </div>
              </div>

              <div className="space-y-2">
                <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">{formData.coding_system === 'loinc' ? 'LOINC Code' : formData.coding_system === 'snomed' ? 'SNOMED Code' : 'Custom Code'}</label>
                <input
                  type="text"
                  placeholder={formData.coding_system === 'loinc' ? 'e.g. 59576-9' : 'e.g. SNOMED-123'}
                  className={fieldInput}
                  value={formData.code}
                  onChange={e => setFormData(prev => ({ ...prev, code: e.target.value }))}
                />
              </div>
            </div>
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
                onClick={() => handleSubmit()}
                disabled={loading || !formData.title || !selectedTypeId}
                className="px-8 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-xl font-bold text-sm shadow-lg shadow-blue-500/20 transition-all flex items-center space-x-2"
              >
                {loading ? (
                  <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent" />
                ) : (
                  <Save className="w-4 h-4" />
                )}
                <span>{submitLabel ?? (editMode ? t('events.update_event') : t('events.save_event'))}</span>
              </button>
            </div>
          </div>
        )}

        <ExaminationSelectorModal
          isOpen={isSelectorOpen}
          onClose={() => setIsSelectorOpen(false)}
          examinations={examinations}
          selectedIds={formData.examinations.map(e => e.examination_id)}
          onSelectionChange={handleSelectionChange}
        />

        <ObservationSelectorModal
          isOpen={isObsSelectorOpen}
          onClose={() => setIsObsSelectorOpen(false)}
          observations={patientObservations}
          selectedIds={formData.observations.map(o => o.observation_id)}
          onSelectionChange={handleObsSelectionChange}
        />
      </div>
    );
  }
);
