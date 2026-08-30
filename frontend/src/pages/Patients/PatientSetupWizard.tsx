import { useState, useEffect, useCallback, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ChevronLeft, ChevronRight, CheckCircle2, PanelRightClose } from 'lucide-react';
import { getPatient } from '../../services/patientService';
import { getSetupChecklist } from '../../services/setupService';
import type { Patient } from '../../types/patient';
import type { SetupChecklist, SetupStep } from '../../types/setup';
import { LoadingState } from '../../components/ui/LoadingState';
import { SetupLayout } from '../../components/setup/SetupLayout';
import { StepRenderer } from '../../components/setup/steps/StepRenderer';
import { ManualCompleteToggle } from '../../components/setup/steps/ManualCompleteToggle';
import type { SetupStepListView } from '../../components/setup/SetupStepList';
import { resolveSection } from '../../components/setup/sections/registry';
import { useUIStore } from '../../store/slices/uiSlice';

/**
 * Patient setup wizard — the advanced form, step-organised (design D4).
 *
 * Route: `/patients/:patientId/setup`. Backend-derived (design D2): reads
 * `GET /setup/checklist?entity=patient`, renders one panel per step, and
 * re-polls after each inline-form save so steps flip green in-place. The
 * `completed` bit is authoritative; no local completion state.
 *
 * Reopenable + deep-linkable (design D6) — no first-run gate.
 */
function PatientSetupWizard() {
  const { patientId = '' } = useParams();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const setSetupDrawerOpen = useUIStore((s) => s.setSetupDrawerOpen);
  const setSetupDrawerCollapsed = useUIStore((s) => s.setSetupDrawerCollapsed);
  const setSetupDrawerEntity = useUIStore((s) => s.setSetupDrawerEntity);
  const setSetupWizardActive = useUIStore((s) => s.setSetupWizardActive);

  const [patient, setPatient] = useState<Patient | null>(null);
  const [checklist, setChecklist] = useState<SetupChecklist | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeStepId, setActiveStepId] = useState<string>('');

  // Load patient + checklist in parallel.
  const loadAll = useCallback(async () => {
    try {
      const [p, c] = await Promise.all([
        getPatient(patientId),
        getSetupChecklist({ entity: 'patient', entity_id: patientId }),
      ]);
      setPatient(p);
      setChecklist(c);
      setError(null);
    } catch (err) {
      console.error('Failed to load patient setup wizard', err);
      setError(t('common.error', 'Failed to load.'));
    } finally {
      setLoading(false);
    }
  }, [patientId, t]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // Mark wizard active + set entity context so the drawer shows patient steps.
  useEffect(() => {
    setSetupWizardActive(true);
    setSetupDrawerEntity('patient', patientId);
  }, [patientId, setSetupWizardActive, setSetupDrawerEntity]);

  // Minimize to popup: navigate back to patient detail + open drawer collapsed.
  const minimizeToPopup = () => {
    setSetupDrawerOpen(true);
    setSetupDrawerCollapsed(true);
    navigate(`/patients/${patientId}`);
  };

  // Only show entity (patient) steps in the patient wizard; role steps
  // belong to the role wizard at /setup/wizard.
  const patientSteps = useMemo<SetupStep[]>(
    () => (checklist?.steps ?? []).filter((s) => s.entity === 'patient'),
    [checklist],
  );

  // Default the active step to the first incomplete mandatory step.
  useEffect(() => {
    if (!patientSteps.length) return;
    if (!activeStepId || !patientSteps.some((s) => s.id === activeStepId)) {
      const firstIncomplete = patientSteps.find((s) => !s.completed && !s.optional);
      setActiveStepId((firstIncomplete ?? patientSteps[0]).id);
    }
  }, [patientSteps, activeStepId]);

  const activeStep = patientSteps.find((s) => s.id === activeStepId) ?? patientSteps[0];

  // Stepper view: steps with localised titles.
  const stepperSteps = useMemo<SetupStepListView[]>(
    () =>
      patientSteps.map((s) => ({
        ...s,
        title: t(s.title_i18n_key, s.id),
      })),
    [patientSteps, t],
  );

  const currentIndex = patientSteps.findIndex((s) => s.id === activeStepId);
  const goNext = () => {
    if (currentIndex < patientSteps.length - 1) {
      setActiveStepId(patientSteps[currentIndex + 1].id);
    }
  };
  const goPrevious = () => {
    if (currentIndex > 0) setActiveStepId(patientSteps[currentIndex - 1].id);
  };

  if (loading) {
    return <LoadingState variant="section" message={t('common.loading', 'Loading…')} />;
  }
  if (error || !patient || !checklist || !activeStep) {
    return (
      <div className="max-w-md mx-auto py-20 text-center">
        <p className="text-gray-500 dark:text-dark-muted">{error ?? t('common.not_found', 'Not found.')}</p>
        <button
          onClick={() => navigate(`/patients/${patientId}`)}
          className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-xl text-sm font-semibold"
        >
          {t('common.back', 'Back')}
        </button>
      </div>
    );
  }

  const allComplete = checklist.completion >= 1.0;
  const stepTitle = t(activeStep.title_i18n_key, activeStep.id);

  return (
    <SetupLayout
      title={t('setup.patient.title')}
      breadcrumbs={[
        { label: t('common.patients'), href: '/patients' },
        { label: patient.name?.family ?? patient.name?.text ?? patientId, href: `/patients/${patientId}` },
        { label: t('setup.patient.title') },
      ]}
      steps={stepperSteps}
      activeStepId={activeStepId}
      onSelectStep={setActiveStepId}
      groupLabel={t('setup.patient.group_label')}
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
            {/* Minimize to popup badge */}
            <button
              type="button"
              onClick={minimizeToPopup}
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
            {currentIndex < patientSteps.length - 1 ? (
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
                onClick={() => navigate(`/patients/${patientId}`)}
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
        title={stepTitle}
        onNavigate={(route) => {
          navigate(route);
          setSetupDrawerEntity('patient', patientId);
          setSetupDrawerOpen(true);
        }}
        renderInlineForm={(step) => {
          const entry = resolveSection(step);
          if (!entry) return null;
          const { Component, field } = entry;
          return <Component patient={patient} activeField={field} onSaved={loadAll} />;
        }}
      />

      <div className="pt-2 border-t border-gray-100 dark:border-dark-border mt-2">
        <ManualCompleteToggle
          step={activeStep}
          entity="patient"
          entityId={patientId}
          onChanged={loadAll}
        />
      </div>
    </SetupLayout>
  );
}

export default PatientSetupWizard;
