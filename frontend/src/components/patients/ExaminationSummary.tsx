import React, { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { FileText, Plus, ChevronRight, Calendar, Stethoscope, AlertTriangle, RefreshCw } from 'lucide-react';
import { format, parseISO, isValid, differenceInDays } from 'date-fns';
import { getExaminations } from '../../services/examinationService';
import { useCreateIntent } from '../../hooks/useCreateIntent';
import SummaryCardHeader, { TAG_NEUTRAL, TAG_BLUE } from '../ui/SummaryCardHeader';

interface Props {
  patientId: string;
  /** Optional: skip the network fetch if the parent already has the data. */
  initialExaminations?: any[];
}

const PREVIEW_COUNT = 3;

const ExaminationSummary: React.FC<Props> = ({ patientId, initialExaminations }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const [examinations, setExaminations] = useState<any[]>(initialExaminations ?? []);
  const [loading, setLoading] = useState(!initialExaminations);
  const [error, setError] = useState(false);

  const fetchExams = async () => {
    if (!patientId) return;
    try {
      setLoading(true);
      setError(false);
      const data = await getExaminations(patientId);
      setExaminations(data || []);
    } catch (err) {
      console.error('Failed to fetch examinations for summary card', err);
      setError(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (initialExaminations) {
      setExaminations(initialExaminations);
      setLoading(false);
      return;
    }
    fetchExams();
  }, [patientId, initialExaminations]);

  // Open the upload page if arrived via ?new=examination
  useCreateIntent(() => navigate('/examinations/upload'), 'examination');

  const sorted = useMemo(() => {
    return [...examinations].sort(
      (a, b) => new Date(b.examination_date).getTime() - new Date(a.examination_date).getTime()
    );
  }, [examinations]);

  const recent = sorted.slice(0, PREVIEW_COUNT);
  const lastVisit = sorted[0];

  const lastVisitLabel = useMemo(() => {
    if (!lastVisit?.examination_date) return null;
    const d = parseISO(lastVisit.examination_date);
    if (!isValid(d)) return null;
    const days = differenceInDays(new Date(), d);
    if (days === 0) return t('patients.today');
    if (days === 1) return t('patients.yesterday');
    if (days < 30) return t('patients.days_ago', { count: days });
    return format(d, 'MMM d, yyyy');
  }, [lastVisit, t]);

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
          icon={FileText}
          iconClassName="text-blue-500"
          title={t('patients.examination_history')}
          info={{
            title: t('patients.examination_history'),
            content: t('patients.examination_history_info'),
            ariaLabel: t('common.info'),
          }}
          onAdd={() => navigate('/examinations/upload')}
          addLabel={t('patients.add_visit')}
          onOpen={() => navigate('/examinations')}
          openLabel={t('common.open_x', { x: t('patients.examination_history') })}
        />
        <div className="p-6 flex flex-col items-center justify-center text-center">
          <AlertTriangle className="w-8 h-8 text-amber-400 mb-2" />
          <p className="text-sm text-gray-500 dark:text-dark-muted mb-3">{t('common.error')}</p>
          <button
            onClick={fetchExams}
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
    <span key="total" className={TAG_NEUTRAL}>{examinations.length} {t('patients.total_short')}</span>,
    lastVisitLabel && (
      <span key="last" className={TAG_BLUE}>{t('patients.last_visit')}: {lastVisitLabel}</span>
    ),
  ].filter(Boolean) as React.ReactNode[];

  return (
    <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden w-full h-full flex flex-col">
      <SummaryCardHeader
        icon={FileText}
        iconClassName="text-blue-500"
        title={t('patients.examination_history')}
        info={{
          title: t('patients.examination_history'),
          content: t('patients.examination_history_info'),
          ariaLabel: t('common.info'),
        }}
        tags={tags}
        onAdd={() => navigate('/examinations/upload')}
        addLabel={t('patients.add_visit')}
        onOpen={() => navigate('/examinations')}
        openLabel={t('common.open_x', { x: t('patients.examination_history') })}
      />

      <div className="p-4 sm:p-6 flex-1 flex flex-col">
        {recent.length === 0 ? (
          <div className="flex flex-col items-center justify-center text-center py-6 flex-1">
            <FileText className="w-10 h-10 text-gray-200 dark:text-dark-border mb-2" />
            <p className="text-sm text-gray-400 dark:text-dark-muted italic mb-3">
              {t('patients.no_exams_recorded')}
            </p>
            <button
              onClick={() => navigate('/examinations/upload')}
              className="flex items-center space-x-1.5 px-3 py-1.5 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-lg hover:bg-blue-100 dark:hover:bg-blue-900/30 transition-all text-xs font-bold"
            >
              <Plus className="w-3 h-3" />
              <span>{t('patients.add_visit')}</span>
            </button>
          </div>
        ) : (
          <div className="space-y-2 flex-1">
            {recent.map(exam => (
              <ExaminationRow key={exam.id} exam={exam} t={t} onClick={() => navigate(`/examinations/${exam.id}`)} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

// ---------- Row ----------
const ExaminationRow: React.FC<{ exam: any; t: any; onClick: () => void }> = ({ exam, t, onClick }) => {
  const date = exam.examination_date ? parseISO(exam.examination_date) : null;
  const dateLabel = date && isValid(date) ? format(date, 'MMM d, yyyy') : '—';
  const clinician = exam.doctors && exam.doctors.length > 0 ? exam.doctors.map((d: any) => d.name).join(', ') : null;
  const status = exam.extraction_status || 'pending';

  const statusClasses: Record<string, string> = {
    completed: 'bg-green-50 text-green-700 border-green-100 dark:bg-green-900/20 dark:text-green-400 dark:border-green-900/30',
    failed: 'bg-red-50 text-red-700 border-red-100 dark:bg-red-900/20 dark:text-red-400 dark:border-red-900/30',
    processing: 'bg-yellow-50 text-yellow-700 border-yellow-100 dark:bg-yellow-900/20 dark:text-yellow-400 dark:border-yellow-900/30 animate-pulse',
    pending: 'bg-gray-50 text-gray-600 border-gray-100 dark:bg-dark-bg dark:text-dark-muted dark:border-dark-border',
  };

  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full text-left flex items-center gap-3 p-3 rounded-xl border border-gray-100 dark:border-dark-border bg-gray-50/50 dark:bg-dark-bg/30 hover:border-blue-200 dark:hover:border-blue-900 hover:shadow-sm transition-all group"
    >
      <div className="shrink-0 flex flex-col items-center justify-center w-10 h-10 rounded-lg bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400">
        <Calendar className="w-4 h-4" />
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="px-2 py-0.5 bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 text-[9px] font-black uppercase tracking-widest rounded-full border border-blue-100 dark:border-blue-800/50">
            {exam.category || t('examinations.category_general')}
          </span>
          <span className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-wider">
            {dateLabel}
          </span>
        </div>
        <p className="text-xs text-gray-500 dark:text-dark-muted truncate">
          {clinician ? (
            <span className="inline-flex items-center">
              <Stethoscope className="w-3 h-3 mr-1 text-gray-400" />
              {clinician}
            </span>
          ) : (
            <span className="italic">{t('patients.no_clinician')}</span>
          )}
        </p>
      </div>

      <div className="shrink-0 flex items-center gap-1.5">
        <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[9px] font-black uppercase tracking-wider border ${statusClasses[status] || statusClasses.pending}`}>
          {status}
        </span>
        <ChevronRight className="w-3 h-3 text-gray-300 group-hover:text-blue-500 group-hover:translate-x-0.5 transition-all" />
      </div>
    </button>
  );
};

export default React.memo(ExaminationSummary);
