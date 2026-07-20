import React, { useState, useEffect, useMemo, forwardRef, useImperativeHandle } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Info, Plus, Activity, CheckCircle2, Search, ArrowLeft, Calendar as CalendarIcon, Clock, ChevronDown, Save, X, PauseCircle, HelpCircle
} from 'lucide-react';
import {
  getEventIcon,
  EventTypeCard,
} from './EventTypeCard';
import { buildSearchableIndex, searchTypes } from '../../utils/clinicalEventSearch';
import {
  getEventTypes,
  getEventCategories,
  ClinicalEventType,
  ClinicalEventCategory,
  ClinicalEvent,
  ClinicalEventStatus,
  ClinicalEventCodingSystem,
  RecurrenceFrequency,
  DayOfWeek,
} from '../../services/clinicalEventService';
import { DynamicMetadataForm } from './DynamicMetadataForm';
import { InstanceField } from '../instances/InstanceField';
import '../../features/instances/adapters'; // registers the instance adapters
import type { InstanceSelection } from '../instances/types';
import { CatalogField } from '../catalog/CatalogField';
import type { CatalogSelection } from '../../types/catalog';
import { DatePicker } from '../ui/DatePicker';
import { TimePicker } from '../ui/TimePicker';
import { ScaleSlider } from '../ui/ScaleSlider';

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
  frequency: RecurrenceFrequency.DAILY,
  interval: 1,
  days_of_week: [] as DayOfWeek[],
  time_of_day: '12:00',
};

/**
 * Phase 8b: per-status visual + copy metadata for the segmented Current Status
 * selector. The icon + semantic color communicate state at a glance; the
 * `titleKey` hover hint explains what clicking will do (esp. the End-Date
 * side-effect for state/recurring kinds).
 *
 * `tone` is the Tailwind color stem used in `STATUS_TONE_CLASSES` below — kept
 * as a string so the table is data-only and the class lookup is centralized
 * (Tailwind's static analyzer can see all the class strings in one place).
 */
type StatusTone = 'emerald' | 'slate' | 'amber' | 'gray';

const STATUS_OPTIONS: Array<{
  value: ClinicalEventStatus;
  tone: StatusTone;
  icon: React.ComponentType<{ className?: string }>;
  titleKey: string;
}> = [
  { value: ClinicalEventStatus.ACTIVE, tone: 'emerald', icon: Activity, titleKey: 'events.status_hint_active' },
  { value: ClinicalEventStatus.RESOLVED, tone: 'slate', icon: CheckCircle2, titleKey: 'events.status_hint_resolved' },
  { value: ClinicalEventStatus.ON_HOLD, tone: 'amber', icon: PauseCircle, titleKey: 'events.status_hint_on_hold' },
  { value: ClinicalEventStatus.UNKNOWN, tone: 'gray', icon: HelpCircle, titleKey: 'events.status_hint_unknown' },
];

// Static class strings (Tailwind's JIT scanner needs the full literal strings
// to emit the rules — building them dynamically via template would drop them).
const STATUS_TONE_CLASSES: Record<StatusTone, { selected: string; idle: string }> = {
  emerald: {
    selected: 'bg-emerald-50 dark:bg-emerald-900/20 border-emerald-500/40 text-emerald-700 dark:text-emerald-300 ring-2 ring-emerald-500/20',
    idle: 'hover:border-emerald-300 hover:text-emerald-600 dark:hover:text-emerald-400 hover:bg-emerald-50/50 dark:hover:bg-emerald-900/10',
  },
  slate: {
    selected: 'bg-slate-100 dark:bg-slate-700/40 border-slate-400 dark:border-slate-500 text-slate-700 dark:text-slate-200 ring-2 ring-slate-400/20',
    idle: 'hover:border-slate-300 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700/20',
  },
  amber: {
    selected: 'bg-amber-50 dark:bg-amber-900/20 border-amber-500/40 text-amber-700 dark:text-amber-300 ring-2 ring-amber-500/20',
    idle: 'hover:border-amber-300 hover:text-amber-600 dark:hover:text-amber-400 hover:bg-amber-50/50 dark:hover:bg-amber-900/10',
  },
  gray: {
    selected: 'bg-gray-100 dark:bg-gray-700/40 border-gray-400 dark:border-gray-500 text-gray-600 dark:text-gray-300 ring-2 ring-gray-400/20',
    idle: 'hover:border-gray-300 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/20',
  },
};

// Kind-aware UI helpers live in `utils/clinicalEventForm.ts` so they can be
// unit-tested. Re-exported here for the form's local convenience.
import {
  getScheduleKind,
  shouldShowEndDate,
  kindHintKey,
  computeDurationDays,
  defaultStatusForKind,
} from '../../utils/clinicalEventForm';
import { ScheduleKind } from '../../services/clinicalEventService';

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
  coding_system: ClinicalEventCodingSystem.CUSTOM,
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
    const { t, i18n } = useTranslation();
    const [loading, setLoading] = useState(false);
    const [types, setTypes] = useState<ClinicalEventType[]>([]);
    const [categories, setCategories] = useState<ClinicalEventCategory[]>([]);
    const [selectedTypeId, setSelectedTypeId] = useState<string>('');
    // Phase 8d type-picker state.
    // - `pickerOpen` controls whether the full category/type picker is visible
    //   or whether the form is in the compact "Selected: X — Change" mode.
    // - `activeCategoryId` is `null` for the category overview, or a category
    //   id when drilled into a specific category.
    // - `searchQuery` overrides the drilldown — when non-empty, the picker
    //   shows a flat list of matching types across all categories.
    // - `searchVisible` (Phase 8f) toggles whether the search input is shown.
    //   Form opens with just the category grid; a "Search" pill reveals the
    //   input for power users. Clears + collapses on X.
    const [pickerOpen, setPickerOpen] = useState(true);
    const [activeCategoryId, setActiveCategoryId] = useState<string | null>(null);
    const [searchQuery, setSearchQuery] = useState('');
    const [searchVisible, setSearchVisible] = useState(false);

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
          const [typesData, categoriesData] = await Promise.all([
            getEventTypes(),
            getEventCategories(),
          ]);
          setTypes(typesData);
          setCategories(categoriesData);

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
              coding_system: (event.coding_system as ClinicalEventCodingSystem) || ClinicalEventCodingSystem.CUSTOM,
              code: event.code || '',
            });
            const typeId = resolveInitialTypeId(typesData);
            setSelectedTypeId(typeId);
            const type = typesData.find(ty => ty.id === typeId);
            if (type && type.category_concept_id) setActiveCategoryId(type.category_concept_id);
            // When editing an existing event with a resolved type, start in
            // the collapsed "Selected: X" view — the user is editing fields,
            // not picking a type. They can click "Change" to re-open.
            if (typeId) setPickerOpen(false);

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
              coding_system: prefill.coding_system || ClinicalEventCodingSystem.CUSTOM,
              code: prefill.code || '',
            });
            const typeId = resolveInitialTypeId(typesData);
            setSelectedTypeId(typeId);
            const type = typesData.find(ty => ty.id === typeId);
            if (type && type.category_concept_id) setActiveCategoryId(type.category_concept_id);
            // Same as the `event` branch: if prefill resolved to a type,
            // collapse the picker so the user lands on the form fields.
            if (typeId) setPickerOpen(false);
          } else {
            setSelectedTypeId('');
            setActiveCategoryId(null);
            setPickerOpen(true);
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
    const scheduleKind = getScheduleKind(selectedType);

    // Phase 8f: 2-step flow. Step 1 = type picker (categories grid). Step 2 =
    // the actual form. Mutually exclusive — picker replaces the form entirely,
    // no co-existence. Edit mode always skips the picker; prefill-with-type_id
    // sets pickerOpen=false on load (see the init effect) so it skips too.
    const showPicker = !editMode && pickerOpen;

    /**
     * Phase 8h: shared on-select handler for a clinical-event type. Used by
     * both the category-card quick-select chips and the drilldown grid so the
     * "select → advance to form" behavior stays identical. Extracted to a
     * named function (instead of inline) precisely so the chips can call it
     * without duplicating the form-state mutation.
     */
    const handleSelectType = (type: ClinicalEventType) => {
      setSelectedTypeId(type.id);
      // Advance to the form view — picker hides, form fields show.
      setPickerOpen(false);
      setSearchQuery('');
      const kind = getScheduleKind(type);
      setFormData(prev => ({
        ...prev,
        status: defaultStatusForKind(kind),
        resolved_date: kind === ScheduleKind.POINT ? '' : prev.resolved_date,
        event_metadata: {
          recurrence: {
            ...DEFAULT_RECURRENCE,
            is_recurring: kind === ScheduleKind.RECURRING,
            frequency: kind === ScheduleKind.RECURRING ? RecurrenceFrequency.WEEKLY : RecurrenceFrequency.DAILY,
          },
        },
      }));
    };

    // Phase 8d: category-first picker derived state.
    //
    // The picker has three modes:
    //   1. **Overview** — no category selected, search empty → show category
    //      cards (one per category, with type count + description).
    //   2. **Drilldown** — a category is selected, search empty → show the
    //      types in that category with a "Back" button.
    //   3. **Search** — search has text → show a flat list of matching types
    //      across ALL categories. Search overrides any selected category.
    //
    // Phase 8l: the search index precomputes a per-type haystack that
    // includes BOTH the English source strings (always searchable — what
    // the backend stores) AND the current locale's translations (what the
    // UI displays). Rebuilds only when types or language changes, not per
    // keystroke. A Greek user can search "εγκυμο" (display) OR "pregnancy"
    // (English source) and find the same type.
    const normalizedSearch = searchQuery.trim().toLowerCase();
    const isSearching = normalizedSearch.length > 0;
    const isDrilledIn = activeCategoryId !== null && !isSearching;

    const visibleCategories = useMemo(() => {
        // Only categories that actually have at least one type assigned.
        const withCounts = categories
          .map(cat => ({
            cat,
            typeCount: types.filter(t => t.category_concept_id === cat.id).length,
          }))
          .filter(({ typeCount }) => typeCount > 0);
        return withCounts;
      }, [categories, types]);

    // Precomputed multilingual search index. Rebuilds only when the types
    // list changes or the active language changes — NOT per keystroke.
    // The deps array is the contract: i18n.language is a string like 'en'
    // or 'el'; when it changes, the index re-translates and re-builds.
    const searchableIndex = useMemo(
      () => buildSearchableIndex(types, t),
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [types, i18n.language],
    );

    const searchResults = useMemo(() => {
        if (!isSearching) return [];
        return searchTypes(searchableIndex, normalizedSearch);
      }, [searchableIndex, isSearching, normalizedSearch]);

    const typesInActiveCategory = useMemo(() => {
        if (!activeCategoryId) return [];
        return types.filter(t => t.category_concept_id === activeCategoryId);
      }, [types, activeCategoryId]);

    const filteredTypes = isSearching
      ? searchResults
      : isDrilledIn
        ? typesInActiveCategory
        : [];

    const handleSubmit = async (e?: React.FormEvent) => {
      if (e) e.preventDefault();
      if (loading || !formData.title || !selectedTypeId) return;
      setLoading(true);
      try {
        // Flatten the recurrence sub-object to the top-level keys the adapter
        // reads (`event_metadata.frequency` / `interval` / `days_of_week` /
        // `time_of_day`). The form keeps the nested `recurrence` shape
        // internally for legacy reasons; on submit we promote the fields so
        // `adaptClinicalEventToEvents` produces per-day occurrences for
        // `schedule_kind='recurring'` types.
        const recurrence = formData.event_metadata?.recurrence;
        const flattenedMetadata: Record<string, any> = { ...formData.event_metadata };
        if (recurrence && recurrence.is_recurring) {
          flattenedMetadata.frequency = recurrence.frequency;
          flattenedMetadata.interval = recurrence.interval;
          flattenedMetadata.days_of_week = recurrence.days_of_week;
          flattenedMetadata.time_of_day = recurrence.time_of_day;
        }

        const payload: ClinicalEventFormPayload = {
          ...formData,
          patient_id: patientId,
          type_id: selectedTypeId,
          event_metadata: flattenedMetadata,
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

    const updateExamReason = (examId: string, reason: string) => {
      setFormData({
        ...formData,
        examinations: formData.examinations.map(e =>
          e.examination_id === examId ? { ...e, reason } : e
        ),
      });
    };

    // Selection (InstancePicker) <-> formData.examinations (carries reason).
    // The picker owns selection; the form owns the per-link reason, preserved
    // across add/remove via this mapping.
    const examSelections: InstanceSelection[] = formData.examinations.map(e => ({
      type: 'examination',
      id: e.examination_id,
    }));
    const onExamSelectionChange = (next: InstanceSelection[]) => {
      const reasonById = new Map(formData.examinations.map(e => [e.examination_id, e.reason]));
      setFormData({
        ...formData,
        examinations: next.map(sel => ({
          examination_id: sel.id,
          reason: reasonById.get(sel.id) || '',
        })),
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

    const obsSelections: InstanceSelection[] = formData.observations.map(o => ({
      type: 'observation',
      id: o.observation_id,
    }));
    const onObsSelectionChange = (next: InstanceSelection[]) => {
      const notesById = new Map(formData.observations.map(o => [o.observation_id, o.notes]));
      setFormData({
        ...formData,
        observations: next.map(sel => ({
          observation_id: sel.id,
          notes: notesById.get(sel.id) || '',
        })),
      });
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
                  {getEventIcon(selectedType?.slug || 'default')}
                </div>
              </div>
              <div>
                <h2 className="text-xl font-bold text-gray-900 dark:text-dark-text tracking-tight">
                  {editMode ? t('events.update_title') : t('events.new_title')}
                </h2>
                <div className="flex items-center mt-1 space-x-2">
                  <span className="text-[10px] text-gray-500 dark:text-dark-muted font-bold uppercase tracking-widest">
                    {selectedType
                      ? t(`events.type.${selectedType.slug}.name`, selectedType.name)
                      : t('events.select_type')}
                  </span>
                  {selectedType?.category_concept && (
                    <>
                      <span className="w-1 h-1 rounded-full bg-gray-300" />
                      <span className="text-[10px] text-blue-500 font-black uppercase tracking-widest">
                        {t(`events.category.${selectedType.category_concept.slug}.name`, selectedType.category_concept.name)}
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

        {/* Phase 8f: 2-step flow. Step 1 = type picker (categories grid).
            Step 2 = the actual form. Mutually exclusive — picker replaces
            the form entirely, no co-existence. Selecting a type advances to
            the form; the "Change" button on the selected-type bar returns
            to the picker. Edit mode + prefill-with-type_id skip the picker. */}
        {showPicker ? (
          <div className="flex-1 min-h-0 overflow-y-auto p-8 space-y-5 custom-scrollbar">
            {/* Search — opt-in via the "🔍 Search" pill. Default view is the
                category grid; clicking the pill reveals the input with
                autoFocus. Clearing (X) collapses back to the pill. */}
            {searchVisible ? (
              <div className="relative">
                <Search className="w-4 h-4 text-gray-400 absolute left-4 top-1/2 -translate-y-1/2 pointer-events-none" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={e => setSearchQuery(e.target.value)}
                  placeholder={t('events.search_types_placeholder', 'Search by name, description or category...')}
                  autoFocus
                  className="w-full pl-11 pr-10 py-3 bg-gray-50 dark:bg-dark-bg border border-transparent rounded-2xl text-sm font-medium text-gray-900 dark:text-dark-text placeholder:text-gray-400 placeholder:font-medium focus:ring-4 focus:ring-blue-500/10 focus:bg-white dark:focus:bg-dark-surface outline-none transition-all"
                />
                <button
                  type="button"
                  onClick={() => {
                    setSearchQuery('');
                    setSearchVisible(false);
                  }}
                  aria-label={t('common.clear', 'Clear')}
                  className="absolute right-3 top-1/2 -translate-y-1/2 p-1 rounded-full text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 hover:bg-gray-200 dark:hover:bg-dark-hover transition-colors"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            ) : (
              <div className="flex justify-end">
                <button
                  type="button"
                  onClick={() => setSearchVisible(true)}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-[10px] font-black uppercase tracking-widest text-gray-500 hover:text-blue-600 dark:hover:text-blue-400 bg-gray-50 dark:bg-dark-bg hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-lg border border-transparent hover:border-blue-200 dark:hover:border-blue-800/40 transition-colors"
                >
                  <Search className="w-3.5 h-3.5" />
                  {t('events.search', 'Search')}
                </button>
              </div>
            )}

            {/* Drilldown header: "← Back to categories" + active category title */}
            {isDrilledIn && (
              <div className="flex items-center justify-between">
                <button
                  type="button"
                  onClick={() => setActiveCategoryId(null)}
                  className="flex items-center gap-1.5 px-2 py-1 text-[11px] font-black uppercase tracking-widest text-gray-500 hover:text-blue-600 dark:hover:text-blue-400 transition-colors"
                >
                  <ArrowLeft className="w-3.5 h-3.5" />
                  {t('events.back_to_categories', 'Back to categories')}
                </button>
                <div className="flex items-center gap-1.5 text-[10px] font-black uppercase tracking-widest text-gray-400 dark:text-dark-muted">
                  {(() => {
                          const cat = categories.find(c => c.id === activeCategoryId);
                          if (!cat) return null;
                          return (
                            <>
                              <span style={{ color: cat.color || undefined }}>{getEventIcon(cat.slug)}</span>
                              <span>{t(`events.category.${cat.slug}.name`, cat.name)}</span>
                            </>
                          );
                  })()}
                </div>
              </div>
            )}

            {/* Overview: category cards (default view, no search, no drilldown) */}
            {!isSearching && !isDrilledIn && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {visibleCategories.map(({ cat, typeCount }) => {
                  // Phase 8h: surface the types inside each category as
                  // quick-select chips. Clicking a chip selects that type
                  // directly (skipping the drilldown); clicking the card body
                  // still drills in. Nested <button> is invalid HTML, so the
                  // outer card is a div with role="button" + keyboard handler.
                  const catTypes = types.filter(t => t.category_concept_id === cat.id);
                  const MAX_CHIPS = 5;
                  const visibleChips = catTypes.slice(0, MAX_CHIPS);
                  const overflow = catTypes.length - visibleChips.length;
                  return (
                    <div
                      key={cat.id}
                      role="button"
                      tabIndex={0}
                      onClick={() => setActiveCategoryId(cat.id)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          setActiveCategoryId(cat.id);
                        }
                      }}
                      className="group flex flex-col p-4 rounded-2xl border border-transparent bg-gray-50 dark:bg-dark-bg hover:border-blue-200 dark:hover:border-blue-700/50 hover:bg-white dark:hover:bg-dark-surface transition-all text-left cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-500/20"
                    >
                      <div className="flex items-start gap-4">
                        <div
                          className="p-3 rounded-2xl bg-white dark:bg-dark-surface shadow-sm flex-shrink-0 group-hover:scale-105 transition-transform"
                          style={{ color: cat.color || '#3b82f6' }}
                        >
                          {getEventIcon(cat.slug)}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-sm font-black text-gray-900 dark:text-dark-text uppercase tracking-tight truncate">
                              {t(`events.category.${cat.slug}.name`, cat.name)}
                            </span>
                            <span className="text-[9px] font-black text-gray-400 dark:text-dark-muted bg-gray-100 dark:bg-dark-hover px-2 py-0.5 rounded-full uppercase tracking-widest flex-shrink-0">
                              {typeCount}
                            </span>
                          </div>
                          {/* Phase 8i: prefer the localized description; fall back to
                              the backend description if no translation exists; hide
                              entirely if neither is present. */}
                          {(() => {
                            const desc = t(`events.category.${cat.slug}.description`, cat.description || '');
                            if (!desc) return null;
                            return (
                              <p className="text-[11px] text-gray-500 dark:text-dark-muted mt-1 line-clamp-2 leading-snug">
                                {desc}
                              </p>
                            );
                          })()}
                        </div>
                      </div>

                      {/* Type chips — quick-select shortcuts.
                          Each chip selects its type and advances to the form,
                          skipping the drilldown. Clicks stop propagation so
                          they don't also trigger the card's drilldown. */}
                      {visibleChips.length > 0 && (
                        <div className="mt-3 pt-3 border-t border-gray-100 dark:border-dark-border flex flex-wrap gap-1.5">
                          {visibleChips.map(type => (
                            <button
                              key={type.id}
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                handleSelectType(type);
                              }}
                              className="flex items-center gap-1.5 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider rounded-lg bg-white dark:bg-dark-surface text-gray-600 dark:text-gray-300 hover:bg-blue-50 dark:hover:bg-blue-900/30 hover:text-blue-700 dark:hover:text-blue-300 hover:border-blue-200 dark:hover:border-blue-700/50 border border-gray-100 dark:border-dark-border transition-colors"
                              title={t(`events.type.${type.slug}.description`, type.description || type.name)}
                            >
                              {type.color && (
                                <span
                                  className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                                  style={{ backgroundColor: type.color }}
                                />
                              )}
                              {t(`events.type.${type.slug}.name`, type.name)}
                            </button>
                          ))}
                          {overflow > 0 && (
                            <span className="px-2 py-1 text-[10px] font-black uppercase tracking-wider text-gray-400 dark:text-dark-muted self-center">
                              +{overflow}
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* Type grid (drilldown into a category OR search results) */}
            {(isSearching || isDrilledIn) && (
              <>
                {/* Search empty-state */}
                {isSearching && filteredTypes.length === 0 && (
                  <div className="p-8 text-center bg-gray-50 dark:bg-dark-bg/50 rounded-2xl border border-dashed border-gray-200 dark:border-dark-border">
                    <p className="text-xs text-gray-400 italic">
                      {t('events.no_types_match', 'No event types match your search.')}
                    </p>
                  </div>
                )}

                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
                  {filteredTypes.map(type => (
                    <EventTypeCard
                      key={type.id}
                      type={type}
                      isSelected={selectedTypeId === type.id}
                      onSelect={handleSelectType}
                      // Phase 8j: in search mode, types from multiple categories
                      // are mixed — show the parent category as a hint. In
                      // drilldown mode, the category is already implied by the
                      // active filter, so skip the hint to reduce clutter.
                      showCategoryHint={isSearching}
                      // Description is always shown (the whole point of Phase 8j —
                      // was previously only a tooltip on chips/cards).
                      showDescription
                    />
                  ))}
                </div>
              </>
            )}
          </div>
        ) : (
        <>
        <form onSubmit={handleSubmit} className="flex-1 min-h-0 overflow-y-auto p-8 space-y-10 custom-scrollbar">
          {/* Selected-type bar — visible whenever the form is shown and a type
              is resolved (new events only). Edit mode hides it. Clicking
              "Change" re-opens the picker, hiding the form entirely. */}
          {!editMode && selectedTypeId && selectedType && (
            <div className="flex items-center justify-between gap-3 p-4 rounded-2xl bg-blue-50/60 dark:bg-blue-900/15 border border-blue-100 dark:border-blue-800/30">
              <div className="flex items-center gap-3 min-w-0">
                <div
                  className="p-2 rounded-xl bg-white dark:bg-dark-surface shadow-sm flex-shrink-0"
                  style={{ color: selectedType.color || '#3b82f6' }}
                >
                  {getEventIcon(selectedType.slug)}
                </div>
                <div className="min-w-0">
                  <div className="text-[10px] font-black uppercase tracking-widest text-blue-600 dark:text-blue-400">
                    {selectedType.category_concept
                      ? t(`events.category.${selectedType.category_concept.slug}.name`, selectedType.category_concept.name)
                      : ''}
                  </div>
                  <div className="text-sm font-bold text-gray-900 dark:text-dark-text truncate">
                    {t(`events.type.${selectedType.slug}.name`, selectedType.name)}
                  </div>
                  {/* Phase 8k: show the selected type's localized description so
                      the user has context for what they picked without going
                      back to the picker. 1-line clamp keeps the bar compact. */}
                  {(() => {
                    const desc = t(`events.type.${selectedType.slug}.description`, selectedType.description || '');
                    if (!desc) return null;
                    return (
                      <p className="text-[10px] text-gray-500 dark:text-dark-muted italic line-clamp-1 leading-snug mt-0.5">
                        {desc}
                      </p>
                    );
                  })()}
                </div>
              </div>
              <button
                type="button"
                onClick={() => setPickerOpen(true)}
                className="flex-shrink-0 px-3 py-1.5 text-[10px] font-black uppercase tracking-widest text-blue-600 dark:text-blue-400 bg-white dark:bg-dark-surface hover:bg-blue-50 dark:hover:bg-blue-900/30 border border-blue-200 dark:border-blue-800/40 rounded-lg transition-colors"
              >
                {t('events.change_type', 'Change')}
              </button>
            </div>
          )}

          {/* Phase 8f: Current Status promoted to full-width above the 2-col
              grid. The 4-segment control needs horizontal room (was squeezed
              in the narrow right column); the related-items columns benefit
              from vertical stacking. */}
          <div className="space-y-2">
            <label className="block text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em] ml-1">{t('events.current_status')}</label>
            {/* Phase 8b: modern segmented control.
                Icon + label per status, semantic color on selection, native
                `title` hover hint explaining the End-Date side-effect for
                ongoing kinds. Replaces the previous 2×2 grid of identical
                text-only buttons.
                Always 4 columns (one row). At full container width each cell
                has plenty of room — no truncation. */}
            <div role="group" aria-label={t('events.current_status')} className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {STATUS_OPTIONS.map(opt => {
                const isSelected = formData.status === opt.value;
                const toneClasses = STATUS_TONE_CLASSES[opt.tone];
                const Icon = opt.icon;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    role="button"
                    aria-pressed={isSelected}
                    title={t(opt.titleKey, { defaultValue: '' })}
                    onClick={() => {
                      // Status drives the resolved_date semantics for
                      // state/recurring kinds: entering RESOLVED defaults
                      // the End Date to today (user can edit). Leaving
                      // RESOLVED clears it so stale data never persists.
                      const isOngoingKind = scheduleKind === ScheduleKind.STATE || scheduleKind === ScheduleKind.RECURRING;
                      const todayIso = new Date().toISOString().split('T')[0];
                      setFormData(prev => {
                        let nextResolved = prev.resolved_date;
                        if (isOngoingKind) {
                          if (opt.value === ClinicalEventStatus.RESOLVED) {
                            if (!prev.resolved_date) nextResolved = todayIso;
                          } else {
                            nextResolved = '';
                          }
                        }
                        // range kind: keep whatever the user entered.
                        return { ...prev, status: opt.value, resolved_date: nextResolved };
                      });
                    }}
                    className={`flex items-center justify-center gap-2 px-4 py-3 rounded-xl text-xs font-black uppercase tracking-wider transition-all border min-w-0 ${
                      isSelected ? toneClasses.selected : `bg-white dark:bg-dark-bg border-gray-100 dark:border-dark-border text-gray-400 ${toneClasses.idle}`
                    }`}
                  >
                    <Icon className="w-4 h-4 flex-shrink-0" />
                    <span className="truncate block text-center leading-tight">{t(`events.status.${opt.value.toLowerCase()}`)}</span>
                  </button>
                );
              })}
            </div>
          </div>

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

              {/* Date field(s) — adapt to the type's schedule_kind and status.
                  - state     -> Onset only; End Date appears when status=RESOLVED
                  - range     -> Start + End Date (always — bounded by definition)
                  - point     -> single Date (instantaneous, never an end date)
                  - recurring -> Start + recurrence block; End Date appears when
                                  status=RESOLVED (schedule ended)
                  Phase 8a: schedule_kind is required on the wire; the STATE
                  fallback in getScheduleKind is a runtime safety net only. */}
              <div className="space-y-1">
                {(() => {
                  const showEnd = shouldShowEndDate(scheduleKind, formData.status);
                  const startLabel = scheduleKind === ScheduleKind.POINT
                    ? t('events.date_single')
                    : (scheduleKind === ScheduleKind.RANGE ? t('events.date_start') : t('events.date_onset'));
                  const endLabel = scheduleKind === ScheduleKind.RANGE
                    ? t('events.date_end')
                    : t('events.date_end'); // same key works for both contexts

                  // Duration copy when both dates are present.
                  const durationDays = showEnd
                    ? computeDurationDays(formData.onset_date, formData.resolved_date)
                    : null;

                  return (
                    <>
                      <div className={showEnd ? 'grid grid-cols-2 gap-6' : 'space-y-1'}>
                        <div className="space-y-1">
                          <label className="block text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em] ml-1 mb-2">
                            {startLabel}
                          </label>
                          <DatePicker
                            value={formData.onset_date}
                            onChange={date => setFormData({ ...formData, onset_date: date })}
                            allowClear
                          />
                        </div>
                        {showEnd && (
                          <div className="space-y-1">
                            <label className="block text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em] ml-1 mb-2">
                              {endLabel}
                            </label>
                            <DatePicker
                              value={formData.resolved_date}
                              onChange={date => setFormData({ ...formData, resolved_date: date })}
                              // Phase 8c: the End Date for `range` kind is
                              // user-owned — let the user clear it directly.
                              // For state/recurring kinds the End Date is
                              // status-driven, so clearing happens via the
                              // status selector (no clear button here).
                              allowClear={scheduleKind === ScheduleKind.RANGE}
                            />
                          </div>
                        )}
                      </div>
                      <div className="flex items-center gap-3 ml-1 mt-1 flex-wrap">
                        <p className="text-[10px] text-gray-400 dark:text-dark-muted italic">
                          {t(kindHintKey(scheduleKind, formData.status))}
                        </p>
                        {durationDays !== null && (
                          <span className="text-[10px] font-black text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20 px-2 py-0.5 rounded-md border border-blue-100 dark:border-blue-800/30 uppercase tracking-wider">
                            {formData.status === ClinicalEventStatus.RESOLVED
                              ? t('events.duration_resolved', { days: durationDays })
                              : t('events.duration_active', { days: durationDays })}
                          </span>
                        )}
                      </div>
                    </>
                  );
                })()}
              </div>
            </div>

            <div className="space-y-8">
              {/* Related Examinations */}
              <InstanceField
                label={t('events.related_visits')}
                allowedTypes={['examination']}
                patientId={patientId}
                mode="multi"
                displayMode="cards"
                value={examSelections}
                onChange={onExamSelectionChange}
                renderCardFooter={(sel) => (
                  <input
                    type="text"
                    placeholder={t('events.reason_for_visit_placeholder')}
                    className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-transparent rounded-xl text-[11px] font-semibold focus:ring-4 focus:ring-indigo-500/10 outline-none transition-all placeholder:font-medium dark:text-dark-text"
                    value={formData.examinations.find(e => e.examination_id === sel.id)?.reason || ''}
                    onChange={e => updateExamReason(sel.id, e.target.value)}
                  />
                )}
              />

              {/* Related Biomarkers */}
              <InstanceField
                label={t('events.related_biomarkers')}
                allowedTypes={['observation']}
                patientId={patientId}
                mode="multi"
                displayMode="cards"
                value={obsSelections}
                onChange={onObsSelectionChange}
                renderCardFooter={(sel) => (
                  <input
                    type="text"
                    placeholder={t('events.notes_for_biomarker_placeholder')}
                    className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-transparent rounded-xl text-[11px] font-semibold focus:ring-4 focus:ring-blue-500/10 outline-none transition-all placeholder:font-medium dark:text-dark-text"
                    value={formData.observations.find(o => o.observation_id === sel.id)?.notes || ''}
                    onChange={e => updateObservationNotes(sel.id, e.target.value)}
                  />
                )}
              />
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

          {/* Recurrence schedule — shown only for `schedule_kind='recurring'`
              types (e.g. Routine Maintenance). The toggle was removed: the kind
              itself declares recurrence, so the editor is always-on when shown.
              For all other kinds the section is hidden entirely. */}
          {scheduleKind === ScheduleKind.RECURRING && (
            <div className="rounded-3xl p-8 border border-gray-100 dark:border-dark-border bg-gray-50/30 dark:bg-dark-bg/20 space-y-8 animate-in slide-in-from-bottom-4 duration-500 delay-75">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-3">
                  <div className="p-2 bg-blue-50 dark:bg-blue-900/20 text-blue-600 rounded-xl">
                    <CalendarIcon className="w-5 h-5" />
                  </div>
                  <div>
                    <h3 className="text-sm font-bold text-gray-900 dark:text-dark-text uppercase tracking-widest">
                      {t('events.recurrence_for_type')}
                    </h3>
                    <p className="text-[10px] text-gray-400 font-bold uppercase tracking-tighter mt-0.5">
                      {t('events.kind_hint_recurring')}
                    </p>
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                <div className="space-y-4">
                  <label className="block text-[10px] font-black text-gray-400 uppercase tracking-widest ml-1">{t('events.frequency')}</label>
                  <div className="grid grid-cols-3 gap-2">
                    {[RecurrenceFrequency.DAILY, RecurrenceFrequency.WEEKLY, RecurrenceFrequency.MONTHLY].map(f => (
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
                      {formData.event_metadata?.recurrence?.frequency === RecurrenceFrequency.DAILY ? t('events.days') : formData.event_metadata?.recurrence?.frequency === RecurrenceFrequency.WEEKLY ? t('events.weeks') : t('events.months')}
                    </span>
                  </div>
                </div>

                {formData.event_metadata?.recurrence?.frequency === RecurrenceFrequency.WEEKLY && (
                  <div className="md:col-span-2 space-y-4">
                    <label className="block text-[10px] font-black text-gray-400 uppercase tracking-widest ml-1">{t('events.on_days')}</label>
                    <div className="flex flex-wrap gap-2">
                      {[DayOfWeek.MON, DayOfWeek.TUE, DayOfWeek.WED, DayOfWeek.THU, DayOfWeek.FRI, DayOfWeek.SAT, DayOfWeek.SUN].map(day => {
                        const isSelected = formData.event_metadata?.recurrence?.days_of_week?.includes(day);
                        return (
                          <button
                            key={day}
                            type="button"
                            onClick={() => {
                              const current = formData.event_metadata?.recurrence?.days_of_week || [];
                              const updated = isSelected ? current.filter((d: DayOfWeek) => d !== day) : [...current, day];
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
                  <TimePicker
                    value={formData.event_metadata?.recurrence?.time_of_day}
                    onChange={(v) =>
                      setFormData({
                        ...formData,
                        event_metadata: {
                          ...formData.event_metadata,
                          recurrence: { ...formData.event_metadata?.recurrence, time_of_day: v },
                        },
                      })
                    }
                  />
                </div>
              </div>
            </div>
          )}

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
                    const newOccurrence = { date, time, intensity: 5, notes: '', location: null as CatalogSelection | null };
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
                    <div key={i} className="bg-white dark:bg-dark-surface p-6 rounded-2xl border border-gray-100 dark:border-dark-border shadow-sm flex flex-col gap-3 animate-in slide-in-from-top-2">
                      {/* Row 1: date + time + delete */}
                      <div className="flex items-center justify-between">
                        <div className="flex items-center space-x-1">
                          <DatePicker
                            variant="unstyled"
                            className="px-2 py-1.5 bg-gray-50 dark:bg-dark-bg border-none rounded-lg text-[10px] font-bold focus:ring-2 focus:ring-blue-500/20 outline-none w-28 dark:text-dark-text"
                            value={occ.date}
                            allowClear
                            onChange={date => {
                              const updated = [...formData.occurrences];
                              updated[i] = { ...updated[i], date: date };
                              setFormData({ ...formData, occurrences: updated });
                            }}
                          />
                          <TimePicker
                            variant="unstyled"
                            value={occ.time || null}
                            onChange={time => {
                              const updated = [...formData.occurrences];
                              updated[i] = { ...updated[i], time };
                              setFormData({ ...formData, occurrences: updated });
                            }}
                          />
                        </div>
                        <button
                          type="button"
                          onClick={() => {
                            setFormData({
                              ...formData,
                              occurrences: formData.occurrences.filter((_, idx) => idx !== i),
                            });
                          }}
                          className="p-2 text-gray-300 hover:text-red-500 transition-colors"
                        >
                          <X className="w-4 h-4" />
                        </button>
                      </div>
                      {/* Row 2: intensity slider (full width) */}
                      <div className="flex items-center space-x-2">
                        <label className="text-[9px] font-black text-gray-400 uppercase tracking-tighter shrink-0">{t('events.intensity')}</label>
                        <ScaleSlider
                          value={occ.intensity}
                          onChange={(v) => {
                            const updated = [...formData.occurrences];
                            updated[i] = { ...updated[i], intensity: v === '' ? 0 : v };
                            setFormData({ ...formData, occurrences: updated });
                          }}
                          min={1}
                          max={10}
                          lowLabel={t('events.intensity_low', 'Mild')}
                          highLabel={t('events.intensity_high', 'Severe')}
                          className="flex-1"
                        />
                      </div>
                      {/* Row 3: body location (full width — own row so it isn't cropped) */}
                      <CatalogField
                        label={t('events.body_location', 'Body Location')}
                        allowedTypes={['anatomy']}
                        mode="single"
                        value={occ.location ? [occ.location as CatalogSelection] : []}
                        onChange={(next) => {
                          const updated = [...formData.occurrences];
                          updated[i] = { ...updated[i], location: next[0] ?? null };
                          setFormData({ ...formData, occurrences: updated });
                        }}
                        placeholder={t('events.location_placeholder')}
                      />
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
                    <option value={ClinicalEventCodingSystem.LOINC}>LOINC</option>
                    <option value={ClinicalEventCodingSystem.SNOMED}>SNOMED</option>
                    <option value={ClinicalEventCodingSystem.CUSTOM}>Custom</option>
                  </select>
                  <div className="absolute inset-y-0 right-0 flex items-center pr-4 pointer-events-none">
                    <ChevronDown className="w-4 h-4 text-gray-400" />
                  </div>
                </div>
              </div>

              <div className="space-y-2">
                <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">{formData.coding_system === ClinicalEventCodingSystem.LOINC ? 'LOINC Code' : formData.coding_system === ClinicalEventCodingSystem.SNOMED ? 'SNOMED Code' : 'Custom Code'}</label>
                <input
                  type="text"
                  placeholder={formData.coding_system === ClinicalEventCodingSystem.LOINC ? 'e.g. 59576-9' : 'e.g. SNOMED-123'}
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
        </>
        )}
      </div>
    );
  }
);
