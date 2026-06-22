import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Calendar,
  ChevronRight,
  Pill,
  FileText,
  Activity,
  AlertTriangle,
  RefreshCw,
} from 'lucide-react';
import { format, isValid, isToday, isTomorrow, startOfDay, addMonths } from 'date-fns';
import { useCalendarData } from '../../hooks/useCalendarData';
import { CalendarEvent } from '../../types/calendar';
import SummaryCardHeader, { TAG_EMERALD } from '../ui/SummaryCardHeader';

interface Props {
  patientId: string;
}

const PREVIEW_COUNT = 4;
const UPCOMING_WINDOW_MONTHS = 1;

const ScheduleSummary: React.FC<Props> = ({ patientId }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [retryNonce, setRetryNonce] = useState(0);

  // Stabilize the date range so useCalendarData's primitive deps don't change every render
  const startDate = useMemo(() => startOfDay(new Date()), []);
  const endDate = useMemo(() => addMonths(startDate, UPCOMING_WINDOW_MONTHS), [startDate]);

  const { events, loading, error, refresh } = useCalendarData({
    patientId,
    types: ['medication', 'examination', 'clinical-event'],
    startDate,
    endDate,
  });

  // Hook MUST run on every render — keep before any early returns.
  useEffect(() => {
    if (retryNonce > 0) refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [retryNonce]);

  const upcoming = useMemo(() => {
    const today = startOfDay(new Date()).getTime();
    return events
      .filter(e => e.date instanceof Date && e.date.getTime() >= today)
      .sort((a, b) => a.date.getTime() - b.date.getTime())
      .slice(0, PREVIEW_COUNT);
  }, [events]);

  const totalCount = useMemo(
    () => events.filter(e => e.date instanceof Date && e.date.getTime() >= startOfDay(new Date()).getTime()).length,
    [events]
  );

  if (loading) {
    return (
      <div className="animate-pulse bg-white dark:bg-dark-surface rounded-2xl p-6 border border-gray-100 dark:border-dark-border w-full h-full">
        <div className="h-4 w-40 bg-gray-200 rounded mb-4" />
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-10 bg-gray-50 rounded-xl" />
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden w-full h-full flex flex-col">
        <SummaryCardHeader
          icon={Calendar}
          iconClassName="text-emerald-500"
          title={t('common.calendar')}
          info={{
            title: t('patients.upcoming_schedule'),
            content: t('patients.upcoming_schedule_info'),
            ariaLabel: t('common.info'),
          }}
          onOpen={() => navigate('/calendar')}
          openLabel={t('common.open_x', { x: t('patients.open_calendar') })}
        />
        <div className="p-6 flex flex-col items-center justify-center text-center">
          <AlertTriangle className="w-8 h-8 text-amber-400 mb-2" />
          <p className="text-sm text-gray-500 dark:text-dark-muted mb-3">{t('common.error')}</p>
          <button
            onClick={() => setRetryNonce(n => n + 1)}
            className="flex items-center space-x-1.5 px-3 py-1.5 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-lg hover:bg-blue-100 dark:hover:bg-blue-900/30 transition-all text-xs font-bold"
          >
            <RefreshCw className="w-3 h-3" />
            <span>{t('common.retry')}</span>
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden w-full h-full flex flex-col">
      <SummaryCardHeader
        icon={Calendar}
        iconClassName="text-emerald-500"
        title={t('patients.upcoming_schedule')}
        info={{
          title: t('patients.upcoming_schedule'),
          content: t('patients.upcoming_schedule_info'),
          ariaLabel: t('common.info'),
        }}
        tags={[
          <span key="upcoming" className={TAG_EMERALD}>{totalCount} {t('patients.upcoming')}</span>,
        ]}
        onOpen={() => navigate('/calendar')}
        openLabel={t('common.open_x', { x: t('patients.open_calendar') })}
      />

      <div className="p-4 sm:p-6 flex-1 flex flex-col">
        {upcoming.length === 0 ? (
          <div className="flex flex-col items-center justify-center text-center py-6 flex-1">
            <Calendar className="w-10 h-10 text-gray-200 dark:text-dark-border mb-2" />
            <p className="text-sm text-gray-400 dark:text-dark-muted italic">
              {t('patients.no_upcoming_schedule')}
            </p>
          </div>
        ) : (
          <div className="space-y-2 flex-1">
            {upcoming.map((item, idx) => (
              <ScheduleRow
                key={`${item.id}-${idx}`}
                item={item}
                t={t}
                onClick={() => navigateToItem(item, navigate)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

// ---------- Navigation ----------
function navigateToItem(item: CalendarEvent, navigate: (path: string) => void) {
  switch (item.type) {
    case 'medication':
      navigate('/medications');
      break;
    case 'examination':
      if (item.originalData?.id) navigate(`/examinations/${item.originalData.id}`);
      else navigate('/examinations');
      break;
    case 'clinical-event':
      if (item.originalData?.id) navigate(`/events/${item.originalData.id}`);
      else navigate('/events');
      break;
    default:
      navigate('/calendar');
  }
}

// ---------- Row ----------
const ScheduleRow: React.FC<{ item: CalendarEvent; t: any; onClick: () => void }> = ({ item, t, onClick }) => {
  const Icon = item.type === 'medication' ? Pill : item.type === 'examination' ? FileText : item.type === 'clinical-event' ? Activity : Calendar;
  const accent =
    item.type === 'medication'
      ? 'bg-pink-50 dark:bg-pink-900/20 text-pink-600 dark:text-pink-400'
      : item.type === 'examination'
      ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400'
      : item.type === 'clinical-event'
      ? 'bg-purple-50 dark:bg-purple-900/20 text-purple-600 dark:text-purple-400'
      : 'bg-gray-100 dark:bg-dark-bg text-gray-400';

  const dateLabel = formatRelativeDate(item.date, t);
  const timeLabel = item.time || null;

  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full text-left flex items-center gap-3 p-2.5 rounded-xl border border-gray-100 dark:border-dark-border bg-gray-50/50 dark:bg-dark-bg/30 hover:border-blue-200 dark:hover:border-blue-900 hover:shadow-sm transition-all group"
    >
      <div className={`shrink-0 flex items-center justify-center w-9 h-9 rounded-lg ${accent}`}>
        <Icon className="w-4 h-4" />
      </div>

      <div className="flex-1 min-w-0">
        <p className="text-sm font-bold text-gray-900 dark:text-dark-text truncate leading-tight">
          {item.title}
        </p>
        {item.subtitle && (
          <p className="text-[10px] text-gray-400 dark:text-dark-muted truncate mt-0.5">{item.subtitle}</p>
        )}
      </div>

      <div className="shrink-0 text-right">
        <p className="text-[10px] font-black text-gray-700 dark:text-dark-text uppercase tracking-wider leading-tight">
          {dateLabel}
        </p>
        {timeLabel && (
          <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-wider">
            {timeLabel}
          </p>
        )}
      </div>

      <ChevronRight className="w-3 h-3 text-gray-300 group-hover:text-blue-500 group-hover:translate-x-0.5 transition-all shrink-0" />
    </button>
  );
};

// ---------- Helpers ----------
function formatRelativeDate(date: Date, t: any): string {
  if (!date || !isValid(date)) return '—';
  if (isToday(date)) return t('patients.today');
  if (isTomorrow(date)) return t('patients.tomorrow');
  return format(date, 'MMM d');
}

export default React.memo(ScheduleSummary);
