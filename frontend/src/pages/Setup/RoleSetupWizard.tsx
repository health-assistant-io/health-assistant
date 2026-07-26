import { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ChevronLeft, ChevronRight, CheckCircle2, PanelRightClose } from 'lucide-react';
import { getSetupChecklist } from '../../services/setupService';
import type { SetupChecklist, SetupStep } from '../../types/setup';
import { useAuthStore } from '../../store/slices/authSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import { LoadingState } from '../../components/ui/LoadingState';
import { SetupLayout } from '../../components/setup/SetupLayout';
import { StepRenderer } from '../../components/setup/steps/StepRenderer';
import { ManualCompleteToggle } from '../../components/setup/steps/ManualCompleteToggle';
import type { StepperStepView } from '../../components/setup/Stepper';

/**
 * Role setup wizard — the per-account guided checklist.
 *
 * Route: `/setup/wizard` (distinct from the unauthenticated first-run
 * admin bootstrap at `/setup`). Backend-derived (design D2): reads
 * `GET /setup/checklist` (no entity) → the role steps for the calling user
 * (USER / ADMIN / MANAGER / SYSTEM_ADMIN). Most role steps are redirect /
 * external_config / derived (no inline forms), so this wizard is a guided
 * tour: each step explains what to do + links to the right place; on return
 * the checklist re-polls and the step flips green.
 *
 * Reopenable + deep-linkable (design D6). Mounted from the user menu + the
 * NoPatientState CTA.
 */
function RoleSetupWizard() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { user } = useAuthStore();
  const setSetupDrawerOpen = useUIStore((s) => s.setSetupDrawerOpen);
  const setSetupDrawerCollapsed = useUIStore((s) => s.setSetupDrawerCollapsed);
  const setSetupWizardActive = useUIStore((s) => s.setSetupWizardActive);
  const setSetupDrawerEntity = useUIStore((s) => s.setSetupDrawerEntity);

  const [checklist, setChecklist] = useState<SetupChecklist | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeStepId, setActiveStepId] = useState<string>('');

  const loadChecklist = useCallback(async () => {
    try {
      const c = await getSetupChecklist();
      setChecklist(c);
      setError(null);
    } catch (err) {
      console.error('Failed to load role setup checklist', err);
      setError(t('common.error', 'Failed to load.'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  // Mark the wizard as active so the floating badge is available after
  // minimize. Clear entity context so the drawer shows role steps.
  useEffect(() => {
    setSetupWizardActive(true);
    setSetupDrawerEntity(null, null);
  }, [setSetupWizardActive, setSetupDrawerEntity]);

  // Minimize: navigate to dashboard + open the drawer in collapsed (badge) mode.
  const minimizeToBadge = () => {
    setSetupDrawerOpen(true);
    setSetupDrawerCollapsed(true);
    navigate('/dashboard');
  };

  useEffect(() => {
    loadChecklist();
  }, [loadChecklist]);

  // Only role steps (entity === null) belong here.
  const roleSteps = useMemo<SetupStep[]>(
    () => (checklist?.steps ?? []).filter((s) => !s.entity),
    [checklist],
  );

  useEffect(() => {
    if (!roleSteps.length) return;
    if (!activeStepId || !roleSteps.some((s) => s.id === activeStepId)) {
      // Default to the first incomplete step — INCLUDING optional. The user
      // works through ALL steps in order; skipping optionals would land them
      // on the last mandatory step (confusing when early optionals are undone).
      const firstIncomplete = roleSteps.find((s) => !s.completed);
      setActiveStepId((firstIncomplete ?? roleSteps[0]).id);
    }
  }, [roleSteps, activeStepId]);

  const activeStep = roleSteps.find((s) => s.id === activeStepId) ?? roleSteps[0];

  const stepperSteps = useMemo<StepperStepView[]>(
    () => roleSteps.map((s) => ({ ...s, title: t(s.title_i18n_key, s.id) })),
    [roleSteps, t],
  );

  const currentIndex = roleSteps.findIndex((s) => s.id === activeStepId);
  const goNext = () => {
    if (currentIndex < roleSteps.length - 1) setActiveStepId(roleSteps[currentIndex + 1].id);
  };
  const goPrevious = () => {
    if (currentIndex > 0) setActiveStepId(roleSteps[currentIndex - 1].id);
  };

  if (loading) {
    return <LoadingState variant="section" message={t('common.loading', 'Loading…')} />;
  }
  if (error || !checklist || !activeStep) {
    return (
      <div className="max-w-md mx-auto py-20 text-center">
        <p className="text-gray-500 dark:text-dark-muted">{error ?? t('common.not_found', 'Not found.')}</p>
      </div>
    );
  }

  const allComplete = checklist.completion >= 1.0;
  const roleLabel = user?.role
    ? t(`common.role_${user.role.toLowerCase()}`, user.role)
    : '';

  return (
    <SetupLayout
      title={t('setup.role.title')}
      breadcrumbs={[{ label: t('common.dashboard'), path: '/dashboard' }, { label: t('setup.role.title') }]}
      steps={stepperSteps}
      activeStepId={activeStepId}
      onSelectStep={setActiveStepId}
      groupLabel={roleLabel || t('setup.role.group_label')}
      completion={checklist.completion}
      footer={
        <>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={goPrevious}
              disabled={currentIndex <= 0}
              className="inline-flex items-center gap-1 px-3 py-2 text-sm font-semibold text-gray-600 dark:text-dark-muted rounded-xl hover:bg-gray-50 dark:hover:bg-dark-border/50 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <ChevronLeft className="w-4 h-4" /> {t('setup.previous')}
            </button>
            {/* Minimize to floating badge */}
            <button
              type="button"
              onClick={minimizeToBadge}
              title={t('setup.minimize', 'Minimize')}
              className="inline-flex items-center justify-center w-9 h-9 text-gray-400 hover:text-gray-600 dark:hover:text-dark-text rounded-xl hover:bg-gray-50 dark:hover:bg-dark-border/50"
            >
              <PanelRightClose className="w-4 h-4" />
            </button>
          </div>
          <div className="flex items-center gap-2">
            {allComplete && (
              <span className="inline-flex items-center gap-1 text-sm text-green-600 dark:text-green-400">
                <CheckCircle2 className="w-4 h-4" /> {t('setup.all_complete')}
              </span>
            )}
            {currentIndex < roleSteps.length - 1 ? (
              <button
                type="button"
                onClick={goNext}
                className="inline-flex items-center gap-1 px-4 py-2 bg-blue-600 text-white rounded-xl text-sm font-semibold hover:bg-blue-700"
              >
                {t('setup.next')} <ChevronRight className="w-4 h-4" />
              </button>
            ) : (
              <button
                type="button"
                onClick={() => navigate('/dashboard')}
                className="inline-flex items-center gap-1 px-4 py-2 bg-green-600 text-white rounded-xl text-sm font-semibold hover:bg-green-700"
              >
                <CheckCircle2 className="w-4 h-4" /> {t('setup.finish')}
              </button>
            )}
          </div>
        </>
      }
    >
      <StepRenderer
        step={activeStep}
        title={t(activeStep.title_i18n_key, activeStep.id)}
        onNavigate={(route) => {
          navigate(route);
          setSetupDrawerOpen(true);
        }}
      />

      <div className="pt-2 border-t border-gray-100 dark:border-dark-border mt-2">
        <ManualCompleteToggle step={activeStep} onChanged={loadChecklist} />
      </div>
    </SetupLayout>
  );
}

export default RoleSetupWizard;
