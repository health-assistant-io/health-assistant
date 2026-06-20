import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { 
  Pill,
  ShieldAlert,
  FileText,
  Activity,
  Settings as SettingsIcon,
  Check,
  X as CloseIcon,
  Layout,
  Grid,
  List as ListIcon,
  TrendingUp,
  Eye,
  EyeOff,
  Maximize2,
  Minimize2
} from 'lucide-react';
import { 
  format, 
  startOfMonth, 
  endOfMonth, 
  addMonths,
  subMonths
} from 'date-fns';
import { getMedicationOccurrences } from '../../../utils/medicationScheduler';
import { adaptClinicalEventToEvents } from '../../../utils/calendarUtils';
import { SummaryModal } from '../../shared/SummaryModal';
import { ExaminationPreview } from '../../examinations/ExaminationPreview';
import { getExaminationDocuments } from '../../../services/examinationService';
import { UniversalCalendar } from '../../ui/UniversalCalendar';
import { getEventSummaryProps } from '../../../utils/summaryModalUtils';

export const UnifiedHealthCalendarCard = React.forwardRef((props: any, ref: any) => {
  const { t } = useTranslation();
  const { id, isEditMode, onRemove, style, className, onMouseDown, onMouseUp, onTouchEnd, children, 
    medications, allergies, examinations, clinicalEvents,
    onUpdateConfig, config
  } = props;
  
  const navigate = useNavigate();
  const [currentDate] = useState(new Date());
  const [searchTerm] = useState('');
  const [selectedEventDocs, setSelectedEventDocs] = useState<any[]>([]);
  const [loadingDocs, setLoadingDocs] = useState(false);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);

  const viewType = config.viewType || 'timeline';
  const selectedCategories = config.categories || ['medications', 'allergies', 'examinations', 'clinical-events'];
  const compactMode = config.compactMode ?? false;
  const hideHeader = config.hideHeader ?? false;
  const fitToContainer = config.fitToContainer ?? false;

  const updateConfig = (newConfig: any) => {
    if (onUpdateConfig) {
      onUpdateConfig(id, { ...config, ...newConfig });
    }
  };

  const toggleCategory = (cat: string) => {
    const newCategories = selectedCategories.includes(cat)
      ? selectedCategories.filter((c: string) => c !== cat)
      : [...selectedCategories, cat];
    updateConfig({ categories: newCategories });
  };

  React.useEffect(() => {
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

  const getEvents = React.useMemo(() => {
    const events: any[] = [];
    const start = startOfMonth(subMonths(currentDate, 1));
    const end = endOfMonth(addMonths(currentDate, 1));

    if (selectedCategories.includes('medications')) {
      const medOccs = getMedicationOccurrences(medications || [], start, end);
      medOccs.forEach(occ => {
        const medicationId = occ.record.code.catalog_id || occ.record.id;
        events.push({
          id: occ.id,
          rawId: occ.record.id,
          navigationPath: `/medications/details/${medicationId}`,
          type: 'medication',
          title: occ.name,
          subtitle: occ.dosage,
          date: occ.date,
          time: occ.time,
          icon: Pill,
          color: 'blue',
          originalData: occ.record
        });
      });
    }

    if (selectedCategories.includes('allergies')) {
      (allergies || []).forEach((allergy: any) => {
        const date = allergy.last_occurrence ? new Date(allergy.last_occurrence) : 
                     (allergy.onset_date ? new Date(allergy.onset_date) : null);
        if (date && date >= start && date <= end) {
          events.push({
            id: `allergy-${allergy.id}`,
            rawId: allergy.id,
            navigationPath: `/patients/${allergy.patient_id}`,
            type: 'allergy',
            title: allergy.code.text,
            subtitle: `${t('allergies.modal.criticality')}: ${allergy.criticality || t('common.unknown')} - ${t('documents_explorer.status')}: ${allergy.clinical_status}`,
            date: date,
            time: format(date, 'HH:mm'),
            icon: ShieldAlert,
            color: 'red',
            originalData: allergy
          });
        }
      });
    }

    if (selectedCategories.includes('examinations')) {
      (examinations || []).forEach((exam: any) => {
        const date = new Date(exam.examination_date);
        if (date >= start && date <= end) {
          events.push({
            id: `exam-${exam.id}`,
            rawId: exam.id,
            navigationPath: `/examinations/${exam.id}`,
            type: 'examination',
            title: exam.category || t('examinations.clinical_examination'),
            subtitle: exam.notes ? exam.notes.substring(0, 50) + '...' : t('examinations.no_notes'),
            date: date,
            time: format(date, 'HH:mm'),
            icon: FileText,
            color: 'indigo',
            originalData: exam
          });
        }
      });
    }

    if (selectedCategories.includes('clinical-events')) {
      (clinicalEvents || []).forEach((ce: any) => {
        const ceEvents = adaptClinicalEventToEvents(ce, start, end);
        ceEvents.forEach(e => {
          events.push({
            ...e,
            icon: Activity,
            color: 'amber'
          });
        });
      });
    }

    return events.filter(e => 
      e.title.toLowerCase().includes(searchTerm.toLowerCase()) || 
      (e.subtitle && e.subtitle.toLowerCase().includes(searchTerm.toLowerCase()))
    ).sort((a, b) => a.date.getTime() - b.date.getTime() || (a.time || '').localeCompare(b.time || ''));
  }, [medications, allergies, examinations, clinicalEvents, selectedCategories, currentDate, searchTerm]);

  return (
    <div 
      ref={ref}
      style={{ ...style, zIndex: (isSettingsOpen ? 100 : style?.zIndex || 1) }}
      className={`${className || ''} bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border flex flex-col relative group overflow-hidden`}
      onMouseDown={onMouseDown}
      onMouseUp={onMouseUp}
      onTouchEnd={onTouchEnd}
    >
      {isEditMode && (
        <div className="absolute top-2 right-2 flex items-center space-x-1 z-50 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={(e) => { e.stopPropagation(); setIsSettingsOpen(!isSettingsOpen); }}
            className={`p-1.5 rounded-lg border shadow-sm transition-all active:scale-95 ${isSettingsOpen ? 'bg-blue-600 text-white border-blue-700' : 'bg-white dark:bg-dark-surface text-gray-400 border-gray-100 dark:border-dark-border hover:bg-gray-50'}`}
          >
            <SettingsIcon className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onRemove(id); }}
            className="p-1.5 bg-white dark:bg-dark-surface text-red-400 rounded-lg border border-gray-100 dark:border-dark-border shadow-sm hover:bg-red-50 transition-all active:scale-95"
          >
            <CloseIcon className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {isSettingsOpen && (
        <div className="absolute inset-0 bg-white/95 dark:bg-dark-surface/95 backdrop-blur-sm z-[60] p-6 animate-in fade-in duration-200 overflow-y-auto custom-scrollbar">
          <div className="flex justify-between items-center mb-6">
            <h3 className="text-sm font-black uppercase tracking-widest text-gray-900 dark:text-dark-text flex items-center">
              <SettingsIcon className="w-4 h-4 mr-2 text-blue-500" />
              Calendar Settings
            </h3>
            <button onClick={() => setIsSettingsOpen(false)} className="p-1.5 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-lg transition-colors">
              <CloseIcon className="w-4 h-4 text-gray-400" />
            </button>
          </div>

          <div className="space-y-6">
            {/* View Mode */}
            <div className="space-y-3">
              <label className="text-[10px] font-black text-gray-400 uppercase tracking-wider">Default View Mode</label>
              <div className="grid grid-cols-2 gap-2">
                {[
                  { id: 'timeline', icon: Layout, label: 'Timeline' },
                  { id: 'classic', icon: Grid, label: 'Calendar' },
                  { id: 'list', icon: ListIcon, label: 'Schedule' },
                  { id: 'history', icon: TrendingUp, label: 'History' }
                ].map(mode => (
                  <button
                    key={mode.id}
                    onClick={() => updateConfig({ viewType: mode.id })}
                    className={`flex items-center space-x-2 px-3 py-2.5 rounded-xl border text-xs transition-all ${viewType === mode.id ? 'bg-blue-600 text-white border-blue-700 shadow-lg shadow-blue-500/20' : 'bg-gray-50 dark:bg-dark-bg text-gray-600 dark:text-gray-400 border-transparent hover:border-gray-200'}`}
                  >
                    <mode.icon className="w-3.5 h-3.5" />
                    <span className="font-bold">{mode.label}</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Display Options */}
            <div className="space-y-3">
              <label className="text-[10px] font-black text-gray-400 uppercase tracking-wider">Display Options</label>
              <div className="space-y-2">
                <button
                  onClick={() => updateConfig({ compactMode: !compactMode })}
                  className="w-full flex items-center justify-between p-3 rounded-xl bg-gray-50 dark:bg-dark-bg border border-transparent hover:border-gray-200 transition-all"
                >
                  <div className="flex items-center space-x-3">
                    {compactMode ? <Minimize2 className="w-4 h-4 text-blue-500" /> : <Maximize2 className="w-4 h-4 text-gray-400" />}
                    <span className="text-xs font-bold text-gray-700 dark:text-dark-text">Compact Card Mode</span>
                  </div>
                  <div className={`w-8 h-4 rounded-full transition-colors relative ${compactMode ? 'bg-blue-600' : 'bg-gray-300'}`}>
                    <div className={`absolute top-0.5 w-3 h-3 bg-white rounded-full transition-all ${compactMode ? 'left-4.5' : 'left-0.5'}`} />
                  </div>
                </button>

                <button
                  onClick={() => updateConfig({ hideHeader: !hideHeader })}
                  className="w-full flex items-center justify-between p-3 rounded-xl bg-gray-50 dark:bg-dark-bg border border-transparent hover:border-gray-200 transition-all"
                >
                  <div className="flex items-center space-x-3">
                    {hideHeader ? <EyeOff className="w-4 h-4 text-blue-500" /> : <Eye className="w-4 h-4 text-gray-400" />}
                    <span className="text-xs font-bold text-gray-700 dark:text-dark-text">Hide Calendar Header</span>
                  </div>
                  <div className={`w-8 h-4 rounded-full transition-colors relative ${hideHeader ? 'bg-blue-600' : 'bg-gray-300'}`}>
                    <div className={`absolute top-0.5 w-3 h-3 bg-white rounded-full transition-all ${hideHeader ? 'left-4.5' : 'left-0.5'}`} />
                  </div>
                </button>

                {viewType === 'classic' && (
                  <button
                    onClick={() => updateConfig({ fitToContainer: !fitToContainer })}
                    className="w-full flex items-center justify-between p-3 rounded-xl bg-gray-50 dark:bg-dark-bg border border-transparent hover:border-gray-200 transition-all"
                  >
                    <div className="flex items-center space-x-3">
                      <Layout className={`w-4 h-4 ${fitToContainer ? 'text-blue-500' : 'text-gray-400'}`} />
                      <span className="text-xs font-bold text-gray-700 dark:text-dark-text">Fit Month to Container</span>
                    </div>
                    <div className={`w-8 h-4 rounded-full transition-colors relative ${fitToContainer ? 'bg-blue-600' : 'bg-gray-300'}`}>
                      <div className={`absolute top-0.5 w-3 h-3 bg-white rounded-full transition-all ${fitToContainer ? 'left-4.5' : 'left-0.5'}`} />
                    </div>
                  </button>
                )}
              </div>
            </div>

            {/* Content Categories */}
            <div className="space-y-3">
              <label className="text-[10px] font-black text-gray-400 uppercase tracking-wider">Enabled Categories</label>
              <div className="space-y-2">
                {[
                  { id: 'medications', icon: Pill, label: 'Medications', color: 'text-blue-500' },
                  { id: 'allergies', icon: ShieldAlert, label: 'Allergies', color: 'text-red-500' },
                  { id: 'examinations', icon: FileText, label: 'Examinations', color: 'text-indigo-500' },
                  { id: 'clinical-events', icon: Activity, label: 'Clinical Events', color: 'text-amber-500' }
                ].map(cat => (
                  <button
                    key={cat.id}
                    onClick={() => toggleCategory(cat.id)}
                    className="w-full flex items-center justify-between p-3 rounded-xl bg-gray-50 dark:bg-dark-bg border border-transparent hover:border-gray-200 transition-all"
                  >
                    <div className="flex items-center space-x-3">
                      <cat.icon className={`w-4 h-4 ${selectedCategories.includes(cat.id) ? cat.color : 'text-gray-400'}`} />
                      <span className="text-xs font-bold text-gray-700 dark:text-dark-text">{cat.label}</span>
                    </div>
                    {selectedCategories.includes(cat.id) ? (
                        <Check className="w-4 h-4 text-blue-600" />
                    ) : (
                        <div className="w-4 h-4 border-2 border-gray-300 rounded" />
                    )}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <button
            onClick={() => setIsSettingsOpen(false)}
            className="w-full mt-8 py-3 bg-blue-600 text-white font-black text-[10px] uppercase tracking-[0.2em] rounded-xl hover:bg-blue-700 transition-all shadow-lg shadow-blue-500/20 active:scale-95"
          >
            Apply & Save
          </button>
        </div>
      )}

      <div className="flex-1 min-h-0 flex flex-col">
        <UniversalCalendar
          events={getEvents}
          compact={compactMode}
          transparent={true}
          title={t('dashboard.cards.health_timeline')}
          subtitle={t('dashboard.cards.unified_schedule')}
          defaultView={viewType as any}
          hideHeader={hideHeader}
          fitToContainer={fitToContainer}
          renderModal={(event, onClose) => {
            const modalProps = getEventSummaryProps(event, t);

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
                {...modalProps}
                mainAction={{
                  label: t('common.view_details'),
                  onClick: () => {
                    const navPath = modalProps.navigationPath || (event as any).navigationPath;
                    if (navPath) {
                      navigate(navPath);
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
          }}
        />
      </div>

      {children}
    </div>
  );
});
