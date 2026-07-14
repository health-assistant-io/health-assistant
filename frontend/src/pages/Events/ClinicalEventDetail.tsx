import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Clock,
  Activity,
  Edit2,
  Trash2,
  FileText,
  ChevronRight,
  Info,
  Calendar,
  Target,
  Zap,
  Network
} from 'lucide-react';
import { 
  getEvent, 
  deleteEvent, 
  ClinicalEventStatus,
  ClinicalEvent
} from '../../services/clinicalEventService';
import { anatomyService } from '../../services/anatomyService';
import type { AnatomyStructure } from '../../types/anatomy';
import { OrganPreview } from '../../components/anatomy/OrganPreview';
import { AnatomyGraphModal } from '../../components/anatomy/AnatomyGraphModal';
import { markerForStructure, useAnatomyAtlas } from '../../components/anatomy/atlas';
import { ExaminationCard } from '../../components/examinations/ExaminationCard';
import { LoadingState } from '../../components/ui/LoadingState';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';

import { useUIStore } from '../../store/slices/uiSlice';
import { ClinicalEventModal } from '../../components/events/ClinicalEventModal';
import { getEventIcon, getEventStatusBadge } from '../../utils/clinicalEventUtils';

const ClinicalEventDetail: React.FC = () => {
  const { eventId } = useParams<{ eventId: string }>();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [event, setEvent] = useState<ClinicalEvent | null>(null);
  const [bodyStructure, setBodyStructure] = useState<AnatomyStructure | null>(null);
  const [isGraphModalOpen, setIsGraphModalOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const showConfirmation = useUIStore(state => state.showConfirmation);
  const figureOrder = useAnatomyAtlas((s) => s.figureOrder);
  const ensureLoaded = useAnatomyAtlas((s) => s.ensureLoaded);

  useEffect(() => { ensureLoaded(); }, [ensureLoaded]);

  const fetchEventData = async () => {
    if (!eventId) return;
    try {
      setLoading(true);
      const eventData = await getEvent(eventId);
      setEvent(eventData);

      const metaBodyLocation = eventData.event_metadata?.body_location as
        | { id?: string; type?: string }
        | undefined;
      const metaBodyPartId = metaBodyLocation?.id;
      if (metaBodyPartId) {
        try {
          const struct = await anatomyService.get(metaBodyPartId);
          setBodyStructure(struct);
        } catch { setBodyStructure(null); }
      } else {
        setBodyStructure(null);
      }
    } catch (err) {
      console.error("Failed to fetch event details", err);
    } finally {
      setLoading(false);
    }
  };


  /**
   * Format a metadata field value for display. Handles the catalog-select
   * value shapes ({type,id,label} single, or an array of them for multi) plus
   * the scalar types (text/number/date/boolean). Anything else falls back to
   * a JSON string so no value silently renders as [object Object].
   */
  const formatMetadataValue = (val: unknown): string => {
    if (val === null || val === undefined || val === '') return '';
    if (typeof val === 'string' || typeof val === 'number' || typeof val === 'boolean') {
      return String(val);
    }
    // Single catalog-select value: {type, id, label}.
    if (typeof val === 'object' && !Array.isArray(val)) {
      const v = val as { label?: string; id?: string };
      return v.label || v.id || JSON.stringify(val);
    }
    // Multi catalog-select value: array of {type, id, label}.
    if (Array.isArray(val)) {
      const labels = val
        .map((item) =>
          typeof item === 'object' && item !== null
            ? (item as { label?: string }).label || (item as { id?: string }).id || ''
            : String(item),
        )
        .filter(Boolean);
      return labels.join(', ');
    }
    return JSON.stringify(val);
  };

  useEffect(() => {
    fetchEventData();
  }, [eventId]);

  const handleDelete = () => {
    if (!event) return;
    showConfirmation({
      title: t('events.delete_title'),
      message: t('events.delete_confirm', { title: event.title }),
      confirmLabel: t('common.delete'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deleteEvent(event.id);
          navigate(`/patients/${event.patient_id}/events`);
        } catch (err) {
          console.error("Failed to delete event", err);
        }
      }
    });
  };

  if (loading) return <LoadingState variant="section" showText={true} />;
  if (!event) return (
    <div className="max-w-7xl mx-auto p-8 text-center">
      <h2 className="text-2xl font-bold text-gray-900 dark:text-dark-text">{t('events.not_found')}</h2>
      <button onClick={() => navigate(-1)} className="mt-4 text-blue-600 hover:underline">{t('common.back')}</button>
    </div>
  );

  return (
    <div className="max-w-7xl mx-auto pb-20 px-4 sm:px-6 lg:px-8">
      <PageHeader
        title={event.title}
        subtitle={event.type_details?.name}
        icon={getEventIcon(event.type_details?.slug || '', "w-6 h-6")}
        breadcrumbs={[
          { label: t('events.title'), path: '/events' }
        ]}
        showBackButton={true}
      />

      <StickyToolbar
        actions={
          <div className="flex items-center space-x-3">
            <button 
              onClick={() => setIsEditModalOpen(true)}
              className="flex items-center space-x-2 px-4 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border text-gray-700 dark:text-dark-text rounded-xl hover:bg-gray-50 dark:hover:bg-dark-border transition-all font-semibold shadow-sm text-sm"
            >
              <Edit2 className="w-4 h-4" />
              <span className="hidden sm:inline">{t('common.edit')}</span>
            </button>
            <button 
              onClick={handleDelete}
              className="p-2 text-gray-400 hover:text-red-600 transition-all"
            >
              <Trash2 className="w-5 h-5" />
            </button>
          </div>
        }
      />

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-8">
        {/* Left Column: Core Details */}
        <div className="xl:col-span-2 space-y-8">
          {/* Summary Card */}
          <div className="bg-white dark:bg-dark-surface rounded-[2rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm">
            <div className="flex items-start justify-between mb-8">
              <div className="flex items-center space-x-4">
                <div 
                  className="p-4 rounded-2xl bg-opacity-10" 
                  style={{ backgroundColor: event.type_details?.color + '20', color: event.type_details?.color }}
                >
                  {getEventIcon(event.type_details?.slug || '', "w-6 h-6")}
                </div>
                <div>
                  <h3 className="text-lg font-black text-gray-900 dark:text-dark-text uppercase tracking-tight">{t('events.clinical_overview')}</h3>
                  <div className="flex flex-wrap items-center gap-y-2 gap-x-4 mt-2">
                    {getEventStatusBadge(event.status, t)}
                    <div className="flex items-center space-x-2 text-xs text-gray-400 font-bold uppercase tracking-wider bg-gray-50 dark:bg-dark-bg/50 px-3 py-1 rounded-lg border border-gray-100 dark:border-dark-border">
                      <Calendar className="w-3 h-3" />
                      <span>{event.onset_date ? new Date(event.onset_date).toLocaleDateString() : t('common.unknown')}</span>
                      <ChevronRight className="w-3 h-3 text-gray-300" />
                      <span>{event.resolved_date ? new Date(event.resolved_date).toLocaleDateString() : (event.status === ClinicalEventStatus.ACTIVE ? t('events.ongoing') : '—')}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {event.description && (
              <div className="bg-gray-50 dark:bg-dark-bg/30 rounded-3xl p-6 mb-8 border border-gray-50 dark:border-dark-border">
                <p className="text-gray-700 dark:text-dark-text leading-relaxed italic">
                  "{event.description}"
                </p>
              </div>
            )}

            {/* Metadata Fields (Pregnancy specific etc.) */}
            {event.type_details?.slug === 'pregnancy' && event.event_metadata && (
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-6 pt-6 border-t border-gray-100 dark:border-dark-border">
                {event.event_metadata.lmp && (
                  <div>
                    <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">{t('events.lmp_date')}</p>
                    <p className="text-sm font-bold text-gray-900 dark:text-dark-text">{new Date(event.event_metadata.lmp).toLocaleDateString()}</p>
                  </div>
                )}
                {event.event_metadata.edd && (
                  <div>
                    <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">{t('events.edd_date')}</p>
                    <div className="flex items-center space-x-2">
                       <p className="text-sm font-bold text-pink-600 dark:text-pink-400">{new Date(event.event_metadata.edd).toLocaleDateString()}</p>
                       {new Date(event.event_metadata.edd) > new Date() && (
                         <span className="px-2 py-0.5 bg-pink-50 dark:bg-pink-900/20 text-pink-500 rounded-lg text-[9px] font-black uppercase">
                            {Math.ceil((new Date(event.event_metadata.edd).getTime() - new Date().getTime()) / (1000 * 60 * 60 * 24))} Days Left
                         </span>
                       )}
                    </div>
                  </div>
                )}
                {event.event_metadata.lmp && (
                  <div>
                    <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">{t('events.gestational_age')}</p>
                    <p className="text-sm font-bold text-gray-900 dark:text-dark-text">
                      {Math.floor((new Date().getTime() - new Date(event.event_metadata.lmp).getTime()) / (1000 * 60 * 60 * 24 * 7))} Weeks
                    </p>
                  </div>
                )}
              </div>
            )}

            {/* Accident Details */}
            {event.type_details?.slug === 'accident' && event.event_metadata && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-6 pt-6 border-t border-gray-100 dark:border-dark-border">
                {event.event_metadata.mechanism && (
                  <div>
                    <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">{t('events.labels.accident.mechanism')}</p>
                    <p className="text-sm font-bold text-gray-900 dark:text-dark-text">{event.event_metadata.mechanism}</p>
                  </div>
                )}
                {event.event_metadata.emergency_flag && (
                  <div>
                    <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">{t('events.emergency_status')}</p>
                    <span className="px-2 py-1 bg-red-50 dark:bg-red-900/20 text-red-600 rounded-lg text-xs font-black uppercase">{t('events.emergency_visit')}</span>
                  </div>
                )}
              </div>
            )}

            {/* Surgical Recovery */}
            {event.type_details?.slug === 'surgical-recovery' && event.event_metadata && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-6 pt-6 border-t border-gray-100 dark:border-dark-border">
                {event.event_metadata.procedure_date && (
                  <div>
                    <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">{t('events.procedure_date')}</p>
                    <p className="text-sm font-bold text-gray-900 dark:text-dark-text">{new Date(event.event_metadata.procedure_date).toLocaleDateString()}</p>
                  </div>
                )}
                {event.event_metadata.wound_status && (
                  <div>
                    <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">{t('events.wound_status')}</p>
                    <p className="text-sm font-bold text-gray-900 dark:text-dark-text">{event.event_metadata.wound_status}</p>
                  </div>
                )}
              </div>
            )}

            {/* Dental Details */}
            {event.type_details?.slug === 'dental' && event.event_metadata && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-6 pt-6 border-t border-gray-100 dark:border-dark-border">
                {event.event_metadata.area && (
                  <div>
                    <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">{t('events.dental_area')}</p>
                    <p className="text-sm font-bold text-gray-900 dark:text-dark-text">{event.event_metadata.area}</p>
                  </div>
                )}
                {event.event_metadata.milestone && (
                  <div>
                    <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">{t('events.dental_milestone')}</p>
                    <p className="text-sm font-bold text-gray-900 dark:text-dark-text">{event.event_metadata.milestone}</p>
                  </div>
                )}
              </div>
            )}

            {/* Vision Details */}
            {event.type_details?.slug === 'vision' && event.event_metadata && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-6 pt-6 border-t border-gray-100 dark:border-dark-border">
                {event.event_metadata.diopters && (
                  <div>
                    <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">{t('events.vision_diopters')}</p>
                    <p className="text-sm font-bold text-gray-900 dark:text-dark-text">{event.event_metadata.diopters}</p>
                  </div>
                )}
                {event.event_metadata.procedure && (
                  <div>
                    <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">{t('events.vision_procedure')}</p>
                    <p className="text-sm font-bold text-gray-900 dark:text-dark-text">{event.event_metadata.procedure}</p>
                  </div>
                )}
              </div>
            )}

            {/* Aesthetic Details */}
            {event.type_details?.slug === 'aesthetic' && event.event_metadata && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-6 pt-6 border-t border-gray-100 dark:border-dark-border">
                {event.event_metadata.product && (
                  <div>
                    <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">{t('events.aesthetic_product')}</p>
                    <p className="text-sm font-bold text-gray-900 dark:text-dark-text">{event.event_metadata.product}</p>
                  </div>
                )}
                {event.event_metadata.region && (
                  <div>
                    <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">{t('events.aesthetic_region')}</p>
                    <p className="text-sm font-bold text-gray-900 dark:text-dark-text">{event.event_metadata.region}</p>
                  </div>
                )}
              </div>
            )}

            {/* Maintenance Details */}
            {event.type_details?.slug === 'maintenance' && event.event_metadata && (
              <div className="grid grid-cols-1 pt-6 border-t border-gray-100 dark:border-dark-border">
                {event.event_metadata.frequency && (
                  <div>
                    <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">{t('events.maintenance_frequency')}</p>
                    <p className="text-sm font-bold text-gray-900 dark:text-dark-text">{event.event_metadata.frequency}</p>
                  </div>
                )}
              </div>
            )}

            {/* Body Location Visualization (all event types) */}
            {bodyStructure && (
              <div className="pt-6 border-t border-gray-100 dark:border-dark-border">
                <div className="flex items-center space-x-2 mb-4">
                  <Network className="w-4 h-4 text-blue-500" />
                  <h3 className="text-sm font-black text-gray-900 dark:text-dark-text uppercase tracking-tight">{t('events.body_location')}</h3>
                </div>
                <div className="flex items-start gap-6">
                  <div className="w-32 flex-shrink-0">
                    <OrganPreview
                      {...markerForStructure(bodyStructure, figureOrder)}
                      label={bodyStructure?.name}
                    />
                  </div>
                  <div className="flex-1 space-y-2">
                    <div className="flex items-center space-x-2">
                      <Target className="w-3.5 h-3.5 text-blue-500" />
                      <p className="text-sm font-bold text-gray-900 dark:text-dark-text">{bodyStructure.name}</p>
                      <span className="text-[9px] bg-gray-100 dark:bg-dark-bg px-1.5 py-0.5 rounded text-gray-400 font-medium uppercase">
                        {bodyStructure.category}
                      </span>
                    </div>
                    {bodyStructure.description && (
                      <p className="text-xs text-gray-500 dark:text-dark-muted leading-relaxed">{bodyStructure.description}</p>
                    )}
                    {bodyStructure.standard_code && (
                      <span className="inline-block text-[9px] bg-blue-50 dark:bg-blue-900/20 px-2 py-0.5 rounded text-blue-500 font-medium uppercase">
                        {bodyStructure.standard_system}: {bodyStructure.standard_code}
                      </span>
                    )}
                    <div className="flex items-center gap-4 mt-3 pt-2 border-t border-gray-50 dark:border-dark-border">
                      <button
                        onClick={() => setIsGraphModalOpen(true)}
                        className="flex items-center gap-1.5 text-[10px] font-bold text-indigo-500 hover:text-indigo-600 transition-colors"
                      >
                        <Network className="w-3.5 h-3.5" />
                        View Relations Graph
                      </button>
                      <button
                        onClick={() => navigate('/anatomy')}
                        className="flex items-center gap-1 text-[10px] font-bold text-blue-500 hover:text-blue-600 transition-colors"
                      >
                        Open Anatomy Explorer →
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {bodyStructure && (
              <AnatomyGraphModal 
                isOpen={isGraphModalOpen} 
                onClose={() => setIsGraphModalOpen(false)} 
                initialStructure={bodyStructure} 
              />
            )}

            {/* Pain Episode Details */}
            {['pain-episode', 'flare-up'].includes(event.type_details?.slug || '') && event.event_metadata && (
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-6 pt-6 border-t border-gray-100 dark:border-dark-border">
                {event.event_metadata.body_location && (
                  <div>
                    <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">Body Location</p>
                    <div className="flex items-center space-x-2">
                       <Target className="w-3.5 h-3.5 text-blue-500" />
                       <p className="text-sm font-bold text-gray-900 dark:text-dark-text">{formatMetadataValue(event.event_metadata.body_location)}</p>
                    </div>
                  </div>
                )}
                {event.event_metadata.intensity && (
                  <div>
                    <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">Intensity</p>
                    <div className="flex items-center space-x-2">
                       <Activity className="w-3.5 h-3.5 text-red-500" />
                       <p className="text-sm font-bold text-gray-900 dark:text-dark-text">{event.event_metadata.intensity} / 10</p>
                    </div>
                  </div>
                )}
                {event.event_metadata.trigger && (
                  <div>
                    <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">Trigger</p>
                    <div className="flex items-center space-x-2">
                       <Zap className="w-3.5 h-3.5 text-yellow-500" />
                       <p className="text-sm font-bold text-gray-900 dark:text-dark-text">{event.event_metadata.trigger}</p>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Specialized Event Details (Catch-all for metadata) */}
            {!['pregnancy', 'accident', 'surgical-recovery', 'dental', 'vision', 'aesthetic', 'maintenance', 'pain-episode', 'flare-up'].includes(event.type_details?.slug || '') && 
             event.event_metadata && Object.keys(event.event_metadata).filter(k => k !== 'recurrence').length > 0 && (
              <div className="pt-6 border-t border-gray-100 dark:border-dark-border">
                <h3 className="text-sm font-black text-gray-900 dark:text-dark-text uppercase tracking-tight mb-4">{t('events.specialized_details')}</h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
                  {Object.entries(event.event_metadata)
                    .filter(([key]) => key !== 'recurrence')
                    .map(([key, value]) => (
                    <div key={key}>
                      <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">{key.replace(/_/g, ' ')}</p>
                      <p className="text-sm font-bold text-gray-900 dark:text-dark-text">
                        {formatMetadataValue(value)}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Occurrences / Episodes */}
          <div className="space-y-6">
            <div className="flex items-center space-x-2 px-2">
              <Clock className="w-5 h-5 text-blue-500" />
              <h3 className="text-lg font-black text-gray-900 dark:text-dark-text uppercase tracking-tight">{t('events.episode_tracking')}</h3>
              <span className="ml-2 px-2 py-0.5 bg-gray-100 dark:bg-dark-bg text-gray-500 rounded-full text-[10px] font-bold">
                {event.occurrences?.length || 0}
              </span>
            </div>

            <div className="space-y-4">
              {event.occurrences && event.occurrences.length > 0 ? (
                [...event.occurrences].reverse().map((occ, i) => (
                  <div key={i} className="bg-white dark:bg-dark-surface p-6 rounded-3xl border border-gray-100 dark:border-dark-border shadow-sm hover:shadow-md transition-all animate-in slide-in-from-bottom-2 duration-300">
                    <div className="flex items-center justify-between mb-4">
                      <div className="flex items-center space-x-3">
                        <div className={`w-3 h-3 rounded-full ${occ.intensity > 7 ? 'bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.5)]' : (occ.intensity > 4 ? 'bg-yellow-500' : 'bg-green-500')}`} />
                        <span className="text-sm font-bold text-gray-900 dark:text-dark-text">
                          {new Date(occ.date).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })}
                          {occ.time && <span className="ml-2 text-gray-400 font-medium">@ {occ.time}</span>}
                        </span>
                      </div>
                      <div className="flex items-center space-x-2">
                        <span className="text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('events.intensity')}</span>
                        <span className={`px-2.5 py-1 rounded-xl text-xs font-black text-white ${occ.intensity > 7 ? 'bg-red-500' : (occ.intensity > 4 ? 'bg-yellow-500' : 'bg-green-500')}`}>
                          {occ.intensity} / 10
                        </span>
                      </div>
                    </div>
                    
                    <div className="flex flex-wrap gap-3 mb-4">
                      {occ.location && (
                        <span className="px-3 py-1 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-full text-[10px] font-black uppercase tracking-widest border border-blue-100 dark:border-blue-800/30">
                          {formatMetadataValue(occ.location)}
                        </span>
                      )}
                    </div>

                    {occ.notes && (
                      <p className="text-sm text-gray-600 dark:text-dark-muted leading-relaxed bg-gray-50 dark:bg-dark-bg/50 p-4 rounded-2xl italic">
                        "{occ.notes}"
                      </p>
                    )}
                  </div>
                ))
              ) : (
                <div className="bg-gray-50 dark:bg-dark-bg/50 rounded-3xl p-12 text-center border border-dashed border-gray-200 dark:border-dark-border">
                  <p className="text-gray-400 italic">{t('events.no_occurrences_hint')}</p>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Right Column: Linked Examinations & Coding */}
        <div className="space-y-8">
          {/* Linked Examinations */}
          <div className="bg-white dark:bg-dark-surface rounded-[2rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm">
            <div className="flex items-center space-x-3 mb-6">
              <div className="p-2 bg-indigo-50 dark:bg-indigo-900/20 rounded-xl">
                <FileText className="w-5 h-5 text-indigo-500" />
              </div>
              <h3 className="text-sm font-black text-gray-900 dark:text-dark-text uppercase tracking-widest">{t('events.related_visits')}</h3>
            </div>

            <div className="space-y-4">
              {event.examinations && event.examinations.length > 0 ? (
                event.examinations.map((link, idx) => (
                  <div key={idx} className="space-y-2">
                    <ExaminationCard 
                      examination={link}
                      variant="compact"
                      showTechnicalDetails={false}
                      showExternalLink={true}
                      allowEventInteraction={false}
                      onClick={() => navigate(`/examinations/${link.id}`)}
                    />
                    {link.reason && (
                      <div className="ml-4 pl-4 border-l-2 border-indigo-50 dark:border-indigo-900/30">
                        <p className="text-[10px] text-gray-500 dark:text-dark-muted italic bg-white/50 dark:bg-dark-surface/50 p-2 rounded-xl border border-gray-100 dark:border-dark-border">
                          "{link.reason}"
                        </p>
                      </div>
                    )}
                  </div>
                ))
              ) : (
                <p className="text-xs text-gray-400 italic text-center py-4">{t('events.no_linked_visits')}</p>
              )}
            </div>
          </div>

          {/* Linked Biomarkers */}
          <div className="bg-white dark:bg-dark-surface rounded-[2rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm">
            <div className="flex items-center space-x-3 mb-6">
              <div className="p-2 bg-blue-50 dark:bg-blue-900/20 rounded-xl">
                <Activity className="w-5 h-5 text-blue-500" />
              </div>
              <h3 className="text-sm font-black text-gray-900 dark:text-dark-text uppercase tracking-widest">{t('events.related_biomarkers')}</h3>
            </div>

            <div className="space-y-4">
              {event.observations && event.observations.length > 0 ? (
                event.observations.map((link, idx) => (
                  <div 
                    key={idx} 
                    onClick={() => {
                      const targetId = link.biomarker_id;
                      if (targetId) navigate(`/biomarkers/details/${targetId}`);
                    }}
                    className="p-4 bg-gray-50 dark:bg-dark-bg/50 rounded-2xl border border-gray-100 dark:border-dark-border group hover:border-blue-200 dark:hover:border-blue-900/30 cursor-pointer hover:shadow-md hover:bg-white dark:hover:bg-dark-surface transition-all"
                  >
                    <div className="flex items-center justify-between mb-2">
                       <h4 className="text-xs font-black text-gray-900 dark:text-dark-text uppercase tracking-tight">
                         {link.code?.text || link.code?.coding?.[0]?.display || link.biomarker_slug || 'Unknown'}
                       </h4>
                       <span className="text-[10px] font-bold text-blue-600 dark:text-blue-400">
                          {link.raw_value} {link.normalized_unit}
                        </span>
                     </div>
                     <div className="flex items-center justify-between">
                        <span className="text-[9px] text-gray-400 font-bold uppercase">
                          {new Date(link.effective_datetime).toLocaleDateString()}
                       </span>
                    </div>
                    {link.notes && (
                      <div className="mt-3 pl-3 border-l-2 border-blue-100 dark:border-blue-900/30">
                        <p className="text-[10px] text-gray-500 dark:text-dark-muted italic">
                          "{link.notes}"
                        </p>
                      </div>
                    )}
                  </div>
                ))
              ) : (
                <p className="text-xs text-gray-400 italic text-center py-4">{t('events.no_biomarkers_linked')}</p>
              )}
            </div>
          </div>

          {/* Clinical Coding */}
          {event.code && (
            <div className="bg-white dark:bg-dark-surface rounded-[2rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm">
              <div className="flex items-center space-x-3 mb-6">
                <div className="p-2 bg-blue-50 dark:bg-blue-900/20 rounded-xl">
                  <Info className="w-5 h-5 text-blue-500" />
                </div>
                <h3 className="text-sm font-black text-gray-900 dark:text-dark-text uppercase tracking-widest">{t('events.internal_coding')}</h3>
              </div>
              
              <div className="space-y-4">
                {event.coding_system && (
                  <div>
                    <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">{t('events.code_system')}</p>
                    <code className="text-xs bg-gray-100 dark:bg-dark-bg px-2 py-1 rounded text-blue-600 dark:text-blue-400 uppercase">{event.coding_system}</code>
                  </div>
                )}
                {event.code && (
                  <div>
                    <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">{t('events.code_value')}</p>
                    <code className="text-xs bg-gray-100 dark:bg-dark-bg px-2 py-1 rounded text-blue-600 dark:text-blue-400">{event.code}</code>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Timing/Creation Info */}
          <div className="px-8 space-y-2">
            <p className="text-[10px] text-gray-400 font-bold uppercase tracking-widest">
              {t('events.created_at')}: {new Date(event.created_at).toLocaleString()}
            </p>
            <p className="text-[10px] text-gray-400 font-bold uppercase tracking-widest">
              {t('events.last_updated')}: {new Date(event.updated_at).toLocaleString()}
            </p>
          </div>
        </div>
      </div>

      <ClinicalEventModal 
        isOpen={isEditModalOpen}
        onClose={() => setIsEditModalOpen(false)}
        patientId={event.patient_id}
        event={event}
        onSuccess={fetchEventData}
      />
    </div>
  );
};

export default ClinicalEventDetail;
