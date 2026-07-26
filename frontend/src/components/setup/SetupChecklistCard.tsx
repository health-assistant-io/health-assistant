import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowRight, CheckCircle2, Sparkles } from 'lucide-react';
import { getSetupChecklist } from '../../services/setupService';
import type { SetupChecklist } from '../../types/setup';
import { SetupProgressRing } from './SetupProgressRing';

interface SetupChecklistCardProps {
  patientId: string;
  /** Compact rendering (used in a sidebar). Defaults to false. */
  compact?: boolean;
}

/**
 * Compact completion card mounted on `PatientDetail`. Shows the mandatory
 * completion ratio + a CTA into the wizard. Hidden when fully complete,
 * replaced by a small "Setup complete" badge.
 *
 * Reads the backend-derived checklist (design D2) — never holds local
 * completion state. When the user returns from the wizard the parent page
 * re-mounts this and it reflects the fresh ratio.
 */
export const SetupChecklistCard: React.FC<SetupChecklistCardProps> = ({ patientId, compact = true }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [checklist, setChecklist] = useState<SetupChecklist | null>(null);

  useEffect(() => {
    let cancelled = false;
    getSetupChecklist({ entity: 'patient', entity_id: patientId })
      .then((c) => {
        if (!cancelled) setChecklist(c);
      })
      .catch((err) => console.error('Failed to load setup checklist', err));
    return () => {
      cancelled = true;
    };
  }, [patientId]);

  if (!checklist) {
    return compact ? null : null;
  }

  const allComplete = checklist.completion >= 1.0;
  const patientSteps = checklist.steps.filter((s) => s.entity === 'patient');
  const mandatoryDone = patientSteps.filter((s) => !s.optional && s.completed).length;
  const mandatoryTotal = patientSteps.filter((s) => !s.optional).length;
  const nextIncomplete = patientSteps.find((s) => !s.completed && !s.optional);

  if (allComplete) {
    return (
      <div className="rounded-2xl bg-green-50 dark:bg-green-900/20 border border-green-100 dark:border-green-800/50 p-4 flex items-center gap-3">
        <CheckCircle2 className="w-5 h-5 text-green-500 shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-bold text-green-700 dark:text-green-300">{t('setup.card_complete')}</p>
        </div>
        <button
          type="button"
          onClick={() => navigate(`/patients/${patientId}/setup`)}
          className="text-xs font-semibold text-green-600 dark:text-green-400 hover:underline"
        >
          {t('setup.open_wizard')}
        </button>
      </div>
    );
  }

  return (
    <div className="rounded-2xl bg-gradient-to-br from-blue-50 to-indigo-50 dark:from-blue-900/20 dark:to-indigo-900/20 border border-blue-100 dark:border-blue-800/40 p-4">
      <div className="flex items-center gap-3 mb-3">
        <SetupProgressRing value={checklist.completion} size={48} stroke={5} />
        <div className="flex-1 min-w-0">
          <p className="text-xs font-bold uppercase tracking-wide text-blue-600 dark:text-blue-300 flex items-center gap-1">
            <Sparkles className="w-3.5 h-3.5" />
            {t('setup.card_title')}
          </p>
          <p className="text-sm font-bold text-brand-navy dark:text-dark-text">
            {mandatoryDone} / {mandatoryTotal}
          </p>
        </div>
      </div>

      {nextIncomplete && (
        <p className="text-[11px] text-blue-700/80 dark:text-blue-200/70 mb-3 truncate">
          {t('setup.next_step_hint', { step: t(nextIncomplete.title_i18n_key, nextIncomplete.id) })}
        </p>
      )}

      <button
        type="button"
        onClick={() => navigate(`/patients/${patientId}/setup`)}
        className="w-full inline-flex items-center justify-center gap-2 px-3 py-2 bg-blue-600 text-white rounded-xl text-sm font-semibold hover:bg-blue-700 transition-colors"
      >
        {t('setup.open_wizard')}
        <ArrowRight className="w-4 h-4" />
      </button>
    </div>
  );
};

export default SetupChecklistCard;
