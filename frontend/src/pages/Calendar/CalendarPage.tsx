import React, { useState, useMemo, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { parseISO, isValid } from 'date-fns';
import { Calendar } from 'lucide-react';
import { UniversalCalendar } from '../../components/ui/UniversalCalendar';
import { NoPatientState } from '../../components/ui/NoPatientState';
import { usePatientStore } from '../../store/slices/patientSlice';
import { CalendarEventType, CalendarEvent } from '../../types/calendar';
import { PageHeader } from '../../components/ui/PageHeader';
import { SummaryModal } from '../../components/shared/SummaryModal';
import { ExaminationPreview } from '../../components/examinations/ExaminationPreview';
import { getExaminationDocuments } from '../../services/examinationService';
import { getEventSummaryProps } from '../../utils/summaryModalUtils';

/** Valid view-type values — anything else falls back to 'timeline'. */
const VALID_VIEWS = ['timeline', 'classic', 'list', 'history'] as const;
type RoutableView = typeof VALID_VIEWS[number];

const DEFAULT_CATEGORIES: CalendarEventType[] = ['medication', 'examination', 'allergy', 'clinical-event'];

const CalendarPage: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { currentPatient } = usePatientStore();

  // Modal state
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [selectedEventDocs, setSelectedEventDocs] = useState<any[]>([]);
  const [loadingDocs, setLoadingDocs] = useState(false);

  // --- URL-driven state: view, date, categories.
  // All three are mirrored in the query string so the calendar is deep-linkable
  // and survives refresh/back navigation. Replaces entries (doesn't pollute
  // history) — matches the CatalogWorkspace pattern.

  const viewParam = searchParams.get('view');
  const view: RoutableView = (VALID_VIEWS as readonly string[]).includes(viewParam || '')
    ? (viewParam as RoutableView)
    : 'timeline';

  const dateParam = searchParams.get('date');
  const parsedDate = dateParam ? parseISO(dateParam) : null;
  const currentDate = parsedDate && isValid(parsedDate) ? parsedDate : new Date();

  const catsParam = searchParams.get('categories');
  const categories: CalendarEventType[] = catsParam
    ? (catsParam.split(',').filter((c): c is CalendarEventType =>
        (DEFAULT_CATEGORIES as string[]).includes(c)
      ))
    : DEFAULT_CATEGORIES;

  const patchParams = useCallback(
    (mut: (prev: URLSearchParams) => void) => {
      setSearchParams(
        (prev) => {
          mut(prev);
          return prev;
        },
        { replace: true }
      );
    },
    [setSearchParams]
  );

  const handleViewChange = useCallback(
    (next: string) => {
      patchParams((prev) => {
        if (next === 'timeline') prev.delete('view');
        else prev.set('view', next);
      });
    },
    [patchParams]
  );

  const handleCurrentDateChange = useCallback(
    (next: Date) => {
      patchParams((prev) => {
        const iso = next.toISOString().slice(0, 10);
        const todayIso = new Date().toISOString().slice(0, 10);
        if (iso === todayIso) prev.delete('date');
        else prev.set('date', iso);
      });
    },
    [patchParams]
  );

  const handleCategoriesChange = useCallback(
    (next: CalendarEventType[]) => {
      patchParams((prev) => {
        // Default set = no param (keeps URLs clean).
        const isDefault =
          next.length === DEFAULT_CATEGORIES.length &&
          DEFAULT_CATEGORIES.every((c) => next.includes(c));
        if (isDefault) prev.delete('categories');
        else prev.set('categories', next.join(','));
      });
    },
    [patchParams]
  );

  useEffect(() => {
    if (selectedEventId) {
      setLoadingDocs(true);
      getExaminationDocuments(selectedEventId)
        .then(docs => {
          setSelectedEventDocs(docs);
        })
        .catch(console.error)
        .finally(() => setLoadingDocs(false));
    } else {
      setSelectedEventDocs([]);
    }
  }, [selectedEventId]);

  const calendarConfig = useMemo(() => ({
    patientId: currentPatient?.id,
    // The fetch types follow the URL-driven filter (so ?categories=medication
    // actually narrows what gets loaded, not just what gets displayed).
    types: categories,
  }), [currentPatient?.id, categories]);

  const renderEventModal = (event: CalendarEvent, onClose: () => void) => {
    const props = getEventSummaryProps(event, t);
    
    if (event.type === 'examination' && event.originalData?.id && selectedEventId !== event.originalData.id) {
      setSelectedEventId(event.originalData.id);
    }
    
    const handleClose = () => {
      setSelectedEventId(null);
      onClose();
    };

    return (
      <SummaryModal
        isOpen={!!event}
        onClose={handleClose}
        {...props}
        mainAction={{
          label: t('common.view_details'),
          onClick: () => {
            if (props.navigationPath) {
              navigate(props.navigationPath);
            }
            handleClose();
          }
        }}
      >
        {event.type === 'examination' && event.originalData && (
          <div className="mt-6 border-t border-gray-100 dark:border-dark-border pt-6">
            {loadingDocs ? (
              <div className="flex flex-col items-center justify-center py-10">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 mb-2"></div>
                <p className="text-xs text-gray-400">Loading examination details...</p>
              </div>
            ) : (
              <ExaminationPreview 
                selectedExam={event.originalData}
                examDocuments={selectedEventDocs}
                hideHeader={true}
                onDocumentClick={(doc) => navigate(`/documents/${doc.id}`)}
                onInfoClick={(b) => b.definitionId && navigate(`/biomarkers/details/${b.definitionId}`)}
              />
            )}
          </div>
        )}

        {event.type === 'allergy' && event.originalData?.reactions && (
          <div className="mt-4">
            <h4 className="text-[10px] font-black text-red-400 uppercase tracking-widest mb-3">Observed Reactions</h4>
            <div className="flex flex-wrap gap-2">
              {event.originalData.reactions.map((r: any, idx: number) => (
                <span key={idx} className="px-4 py-2 bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 rounded-xl text-xs font-black uppercase tracking-wider border border-red-100 dark:border-red-900/30">
                  {r.manifestation}
                </span>
              ))}
            </div>
          </div>
        )}
      </SummaryModal>
    );
  };

  if (!currentPatient) {
    return <NoPatientState icon={Calendar} contextKey="calendar" />;
  }

  return (
    <div className="w-full max-w-full px-2 sm:px-4 lg:px-6 space-y-6 h-[calc(100vh-180px)] flex flex-col overflow-hidden">
      <PageHeader
        title={t('common.calendar')}
        subtitle={t('calendar.full_schedule_for', { name: `${currentPatient.name?.given?.join(' ') ?? ''} ${currentPatient.name?.family ?? ''}`.trim() })}
        icon={<Calendar className="w-8 h-8" />}
      />

      <div className="flex-1 min-h-0 overflow-hidden">
        <UniversalCalendar
          config={calendarConfig}
          hideHeader={false}
          renderModal={renderEventModal}
          view={view}
          onViewChange={handleViewChange}
          currentDate={currentDate}
          onCurrentDateChange={handleCurrentDateChange}
          selectedCategories={categories}
          onSelectedCategoriesChange={handleCategoriesChange}
        />
      </div>
    </div>
  );
};

export default CalendarPage;
