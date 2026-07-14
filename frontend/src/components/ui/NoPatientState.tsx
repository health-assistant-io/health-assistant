import React from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { User, Users, Plus, Settings, type LucideIcon } from 'lucide-react';
import { usePatientStore } from '../../store/slices/patientSlice';

interface NoPatientStateProps {
  /** Lucide icon rendered in the badge. Defaults to User. */
  icon?: LucideIcon;
  /** Override the title. Defaults to t('common.no_patient_title'). */
  title?: string;
  /** Override the description. Takes precedence over context/desc keys. */
  description?: string;
  /**
   * Scope id used to resolve a contextual description, e.g. 'dashboard',
   * 'medications'. Looks up common.no_patient_desc_<context>, falling back
   * to common.no_patient_default_desc when absent.
   */
  contextKey?: string;
  /** Hide the action buttons. Useful when the host page renders its own. */
  showActions?: boolean;
  /** Extra classes for the outer wrapper. */
  className?: string;
}

/**
 * Modular empty-state shown when no patient context is selected.
 *
 * Adapts automatically to the tenant state:
 *  - patients exist  → primary "Select a Patient" (navigates to /patients)
 *  - no patients yet → primary "Create a New Patient" (navigates to /patients?new=patient)
 *
 * A secondary "Manage Patients" action plus a forward-looking hint about an
 * upcoming guided setup wizard are always offered, so new instances get a
 * clear, consistent onboarding cue across every page.
 */
export const NoPatientState: React.FC<NoPatientStateProps> = ({
  icon: Icon = User,
  title,
  description,
  contextKey,
  showActions = true,
  className = '',
}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { patients } = usePatientStore();

  const hasPatients = patients.length > 0;

  const resolvedTitle = title ?? t('common.no_patient_title');
  const resolvedDescription =
    description ??
    (contextKey
      ? t(`common.no_patient_desc_${contextKey}`, {
          defaultValue: t('common.no_patient_default_desc') as string,
        })
      : t('common.no_patient_default_desc'));

  return (
    <div
      className={`flex flex-col items-center justify-center py-20 max-w-lg mx-auto text-center space-y-6 ${className}`}
    >
      <div className="w-20 h-20 bg-blue-50 dark:bg-blue-900/20 rounded-3xl flex items-center justify-center text-blue-600 shadow-inner">
        <Icon className="w-10 h-10" />
      </div>
      <div className="space-y-2">
        <h2 className="text-2xl font-black text-brand-navy dark:text-dark-text tracking-tight">
          {resolvedTitle}
        </h2>
        <p className="text-gray-500 dark:text-dark-muted max-w-md">{resolvedDescription}</p>
      </div>

      {showActions && (
        <div className="flex flex-col w-full gap-3">
          {hasPatients ? (
            <button
              onClick={() => navigate('/patients')}
              className="w-full py-3 bg-blue-600 text-white rounded-2xl font-bold hover:bg-blue-700 transition-all shadow-lg shadow-blue-200 dark:shadow-none inline-flex items-center justify-center gap-2"
            >
              <Users className="w-5 h-5" />
              {t('common.no_patient_select_action')}
            </button>
          ) : (
            <button
              onClick={() => navigate('/patients?new=patient')}
              className="w-full py-3 bg-blue-600 text-white rounded-2xl font-bold hover:bg-blue-700 transition-all shadow-lg shadow-blue-200 dark:shadow-none inline-flex items-center justify-center gap-2"
            >
              <Plus className="w-5 h-5" />
              {t('common.no_patient_create_action')}
            </button>
          )}
          <button
            onClick={() => navigate('/patients')}
            className="w-full py-3 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border text-gray-700 dark:text-dark-text rounded-2xl font-bold hover:bg-gray-50 transition-all inline-flex items-center justify-center gap-2"
          >
            <Users className="w-4 h-4" />
            {t('common.no_patient_manage')}
          </button>
          <button
            onClick={() => navigate('/settings')}
            className="w-full py-3 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border text-gray-700 dark:text-dark-text rounded-2xl font-bold hover:bg-gray-50 transition-all inline-flex items-center justify-center gap-2"
          >
            <Settings className="w-4 h-4" />
            {t('common.no_patient_settings')}
          </button>
        </div>
      )}

      <p className="text-[11px] text-gray-300 dark:text-dark-muted/60 italic">
        {t('common.no_patient_setup_hint')}
      </p>
    </div>
  );
};

export default NoPatientState;