import React, { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Activity, Plus, ChevronRight, AlertTriangle, RefreshCw, Clock } from 'lucide-react';
import { format, parseISO, isValid } from 'date-fns';
import {
  getPatientEvents,
  ClinicalEvent,
  ClinicalEventStatus,
} from '../../services/clinicalEventService';
import { ClinicalEventModal } from '../events/ClinicalEventModal';
import { useCreateIntent } from '../../hooks/useCreateIntent';
import SummaryCardHeader, { TAG_NEUTRAL, TAG_PURPLE } from '../ui/SummaryCardHeader';

interface Props {
  patientId: string;
}

const PREVIEW_COUNT = 3;

const ClinicalEventSummary: React.FC<Props> = ({ patientId }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const [events, setEvents] = useState<ClinicalEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [isModalOpen, setIsModalOpen] = useState(false);

  const fetchEvents = async () => {
    if (!patientId) return;
    try {
      setLoading(true);
      setError(false);
      const data = await getPatientEvents(patientId);
      setEvents(data || []);
    } catch (err) {
      console.error('Failed to fetch clinical events for summary card', err);
      setError(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchEvents();
  }, [patientId]);

  // Open the create modal automatically when arrived via ?new=event
  useCreateIntent(() => setIsModalOpen(true), 'event');

  const activeEvents = useMemo(
    () => events.filter(e => e.status === ClinicalEventStatus.ACTIVE),
    [events]
  );

  // Active first, then most-recently-onset
  const sorted = useMemo(() => {
    return [...events].sort((a, b) => {
      const aActive = a.status === ClinicalEventStatus.ACTIVE ? 0 : 1;
      const bActive = b.status === ClinicalEventStatus.ACTIVE ? 0 : 1;
      if (aActive !== bActive) return aActive - bActive;
      const aDate = a.onset_date ? new Date(a.onset_date).getTime() : 0;
      const bDate = b.onset_date ? new Date(b.onset_date).getTime() : 0;
      return bDate - aDate;
    });
  }, [events]);

  const recent = sorted.slice(0, PREVIEW_COUNT);

  if (loading) {
    return (
      <div className="animate-pulse bg-white dark:bg-dark-surface rounded-2xl p-6 border border-gray-100 dark:border-dark-border w-full h-full">
        <div className="h-4 w-40 bg-gray-200 rounded mb-4" />
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-14 bg-gray-50 rounded-xl" />
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden w-full h-full flex flex-col">
        <SummaryCardHeader
          icon={Activity}
          iconClassName="text-purple-500"
          title={t('events.title')}
          info={{
            title: t('events.title'),
            content: t('events.info_text'),
            ariaLabel: t('common.info'),
          }}
          onAdd={() => setIsModalOpen(true)}
          addLabel={t('events.add_event')}
          titleTo="/events"
        />
        <div className="p-6 flex flex-col items-center justify-center text-center">
          <AlertTriangle className="w-8 h-8 text-amber-400 mb-2" />
          <p className="text-sm text-gray-500 dark:text-dark-muted mb-3">{t('common.error')}</p>
          <button
            onClick={fetchEvents}
            className="flex items-center space-x-1.5 px-3 py-1.5 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-lg hover:bg-blue-100 dark:hover:bg-blue-900/30 transition-all text-xs font-bold"
          >
            <RefreshCw className="w-3 h-3" />
            <span>{t('common.retry')}</span>
          </button>
        </div>
      </div>
    );
  }

  const tags = [
    <span key="total" className={TAG_NEUTRAL}>{events.length} {t('patients.total_short')}</span>,
    activeEvents.length > 0 && (
      <span key="active" className={TAG_PURPLE}>{activeEvents.length} {t('events.active')}</span>
    ),
  ].filter(Boolean) as React.ReactNode[];

  return (
    <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden w-full h-full flex flex-col">
      <SummaryCardHeader
        icon={Activity}
        iconClassName="text-purple-500"
        title={t('events.title')}
        info={{
          title: t('events.title'),
          content: t('events.info_text'),
          ariaLabel: t('common.info'),
        }}
        tags={tags}
        onAdd={() => setIsModalOpen(true)}
        addLabel={t('events.add_event')}
        titleTo="/events"
      />

      <div className="p-4 sm:p-6 flex-1 flex flex-col">
        {recent.length === 0 ? (
          <div className="flex flex-col items-center justify-center text-center py-6 flex-1">
            <Activity className="w-10 h-10 text-gray-200 dark:text-dark-border mb-2" />
            <p className="text-sm text-gray-400 dark:text-dark-muted italic mb-3">
              {t('events.no_events')}
            </p>
            <button
              onClick={() => setIsModalOpen(true)}
              className="flex items-center space-x-1.5 px-3 py-1.5 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-lg hover:bg-blue-100 dark:hover:bg-blue-900/30 transition-all text-xs font-bold"
            >
              <Plus className="w-3 h-3" />
              <span>{t('events.add_event')}</span>
            </button>
          </div>
        ) : (
          <div className="space-y-2 flex-1">
            {recent.map(event => (
              <EventRow key={event.id} event={event} t={t} onClick={() => navigate(`/events/${event.id}`)} />
            ))}
          </div>
        )}
      </div>

      <ClinicalEventModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        patientId={patientId}
        onSuccess={() => {
          setIsModalOpen(false);
          fetchEvents();
        }}
      />
    </div>
  );
};

// ---------- Row ----------
const EventRow: React.FC<{ event: ClinicalEvent; t: any; onClick: () => void }> = ({ event, t, onClick }) => {
  const isActive = event.status === ClinicalEventStatus.ACTIVE;
  const onset = event.onset_date ? parseISO(event.onset_date) : null;
  const onsetLabel = onset && isValid(onset) ? format(onset, 'MMM d, yyyy') : null;
  const resolved = event.resolved_date ? parseISO(event.resolved_date) : null;
  const resolvedLabel = resolved && isValid(resolved) ? format(resolved, 'MMM d, yyyy') : null;

  const statusBadge = (() => {
    const map: Record<string, string> = {
      [ClinicalEventStatus.ACTIVE]: 'bg-purple-50 text-purple-700 border-purple-100 dark:bg-purple-900/20 dark:text-purple-400 dark:border-purple-900/30',
      [ClinicalEventStatus.RESOLVED]: 'bg-green-50 text-green-700 border-green-100 dark:bg-green-900/20 dark:text-green-400 dark:border-green-900/30',
      [ClinicalEventStatus.ON_HOLD]: 'bg-yellow-50 text-yellow-700 border-yellow-100 dark:bg-yellow-900/20 dark:text-yellow-400 dark:border-yellow-900/30',
      [ClinicalEventStatus.UNKNOWN]: 'bg-gray-50 text-gray-600 border-gray-100 dark:bg-dark-bg dark:text-dark-muted dark:border-dark-border',
    };
    return map[event.status] || map[ClinicalEventStatus.UNKNOWN];
  })();

  const typeName = event.type_details?.name || event.type_details?.slug || null;

  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full text-left flex items-center gap-3 p-3 rounded-xl border border-gray-100 dark:border-dark-border bg-gray-50/50 dark:bg-dark-bg/30 hover:border-blue-200 dark:hover:border-blue-900 hover:shadow-sm transition-all group"
    >
      <div className={`shrink-0 flex flex-col items-center justify-center w-10 h-10 rounded-lg ${isActive ? 'bg-purple-50 dark:bg-purple-900/20 text-purple-600 dark:text-purple-400' : 'bg-gray-100 dark:bg-dark-bg text-gray-400'}`}>
        {isActive ? <Activity className="w-4 h-4" /> : <Clock className="w-4 h-4" />}
      </div>

      <div className="flex-1 min-w-0">
        <p className="text-sm font-bold text-gray-900 dark:text-dark-text truncate leading-tight">
          {event.title}
        </p>
        <div className="flex items-center gap-2 mt-0.5 flex-wrap">
          {typeName && (
            <span className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-wider truncate">
              {typeName}
            </span>
          )}
          {onsetLabel && (
            <span className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-wider">
              {resolvedLabel ? `${onsetLabel} — ${resolvedLabel}` : `${t('patients.since')} ${onsetLabel}`}
            </span>
          )}
        </div>
      </div>

      <div className="shrink-0 flex items-center gap-1.5">
        <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[9px] font-black uppercase tracking-wider border ${statusBadge}`}>
          {event.status}
        </span>
        <ChevronRight className="w-3 h-3 text-gray-300 group-hover:text-blue-500 group-hover:translate-x-0.5 transition-all" />
      </div>
    </button>
  );
};

export default React.memo(ClinicalEventSummary);
