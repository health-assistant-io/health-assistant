import React, { useState, useMemo, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { 
  Calendar, 
  User,
  MapPin,
  Pill
} from 'lucide-react';
import { UniversalCalendar } from '../../components/ui/UniversalCalendar';
import { usePatientStore } from '../../store/slices/patientSlice';
import { CalendarEventType, CalendarEvent } from '../../types/calendar';
import { PageHeader } from '../../components/ui/PageHeader';
import { SummaryModal } from '../../components/shared/SummaryModal';
import { ExaminationPreview } from '../../components/examinations/ExaminationPreview';
import { getExaminationDocuments } from '../../services/examinationService';
import { getEventSummaryProps } from '../../utils/summaryModalUtils';

const CalendarPage: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { currentPatient } = usePatientStore();
  const [selectedTypes] = useState<CalendarEventType[]>(['medication', 'examination', 'allergy', 'clinical-event']);
  
  // Modal state
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [selectedEventDocs, setSelectedEventDocs] = useState<any[]>([]);
  const [loadingDocs, setLoadingDocs] = useState(false);

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
    types: selectedTypes
  }), [currentPatient?.id, selectedTypes]);

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
    return (
      <div className="flex flex-col items-center justify-center py-20 bg-gray-50 dark:bg-dark-bg/30 rounded-[3rem] border-4 border-dashed border-gray-100 dark:border-dark-border">
        <Calendar className="w-16 h-16 text-gray-200 mb-6" />
        <h2 className="text-xl font-black text-gray-400 uppercase tracking-widest">{t('common.select_patient_to_view')}</h2>
      </div>
    );
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
          defaultView="timeline"
          hideHeader={false}
          renderModal={renderEventModal}
        />
      </div>
    </div>
  );
};

export default CalendarPage;
