import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { RefreshCw, CheckCircle2, Circle, ExternalLink, ChevronRight, ChevronDown, PanelRightClose, PanelRightOpen, LogOut, Maximize2 } from 'lucide-react';
import { useUIStore } from '../../store/slices/uiSlice';
import { getSetupChecklist } from '../../services/setupService';
import type { SetupChecklist, SetupStep } from '../../types/setup';
import { SetupProgressRing } from './SetupProgressRing';

export const SetupWizardDrawer: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const isOpen = useUIStore((s) => s.setupDrawerOpen);
  const setOpen = useUIStore((s) => s.setSetupDrawerOpen);
  const isCollapsed = useUIStore((s) => s.setupDrawerCollapsed);
  const setCollapsed = useUIStore((s) => s.setSetupDrawerCollapsed);
  const entity = useUIStore((s) => s.setupDrawerEntity);
  const entityId = useUIStore((s) => s.setupDrawerEntityId);
  const setEntity = useUIStore((s) => s.setSetupDrawerEntity);
  const wizardActive = useUIStore((s) => s.setupWizardActive);
  const setWizardActive = useUIStore((s) => s.setSetupWizardActive);

  const [checklist, setChecklist] = useState<SetupChecklist | null>(null);
  const [loading, setLoading] = useState(false);
  const [expandedStepId, setExpandedStepId] = useState<string | null>(null);

  // Context-aware label + routes.
  const isPatientMode = entity === 'patient' && !!entityId;
  const wizardLabel = isPatientMode ? t('setup.patient.title', 'Patient setup') : t('setup.role.title');
  const fullWizardRoute = isPatientMode ? `/patients/${entityId}/setup` : '/setup/wizard';

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const opts = isPatientMode ? { entity: 'patient', entity_id: entityId! } : undefined;
      const c = await getSetupChecklist(opts);
      setChecklist(c);
      if (c) {
        const steps = isPatientMode
          ? c.steps.filter((s) => s.entity === 'patient')
          : c.steps.filter((s) => !s.entity);
        const current = steps.find((s) => s.id === expandedStepId && !s.completed);
        if (!current) {
          const next = steps.find((s) => !s.completed);
          if (next) setExpandedStepId(next.id);
        }
      }
    } catch (err) {
      console.error('Failed to refresh setup checklist', err);
    } finally {
      setLoading(false);
    }
  }, [expandedStepId, isPatientMode, entityId]);

  useEffect(() => {
    if (checklist && !expandedStepId) {
      const steps = isPatientMode
        ? checklist.steps.filter((s) => s.entity === 'patient')
        : checklist.steps.filter((s) => !s.entity);
      const first = steps.find((s) => !s.completed);
      if (first) setExpandedStepId(first.id);
    }
  }, [checklist, expandedStepId, isPatientMode]);

  useEffect(() => {
    if (isOpen) {
      setExpandedStepId(null); // reset on open so the right step auto-expands
      refresh();
    }
  }, [isOpen, entity, entityId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!isOpen || isCollapsed) return;
    const onFocus = () => refresh();
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [isOpen, isCollapsed, refresh]);

  // --- Closed: floating badge ---
  if (!isOpen) {
    if (wizardActive && checklist && checklist.completion < 1.0) {
      return (
        <div className="fixed bottom-6 right-6 z-[540] flex items-center gap-2">
          <button
            onClick={() => { setOpen(true); setCollapsed(false); }}
            className="flex items-center gap-2 px-4 py-2.5 bg-blue-600 text-white rounded-full shadow-lg shadow-blue-300 dark:shadow-none hover:bg-blue-700 transition-colors"
          >
            <PanelRightOpen className="w-4 h-4" />
            <span className="text-sm font-bold">{wizardLabel}</span>
            <span className="text-xs opacity-80">{Math.round(checklist.completion * 100)}%</span>
          </button>
          <button
            onClick={() => setWizardActive(false)}
            title={t('setup.exit_wizard', 'Exit wizard')}
            className="p-1.5 bg-white dark:bg-dark-surface rounded-full shadow border border-gray-200 dark:border-dark-border text-gray-400 hover:text-red-500"
          >
            <LogOut className="w-3.5 h-3.5" />
          </button>
        </div>
      );
    }
    return null;
  }

  // --- Collapsed: floating pill ---
  if (isCollapsed) {
    return (
      <button
        onClick={() => setCollapsed(false)}
        className="fixed bottom-6 right-6 z-[560] flex items-center gap-2 px-4 py-2.5 bg-white dark:bg-dark-surface text-brand-navy dark:text-dark-text rounded-full shadow-lg border border-gray-200 dark:border-dark-border hover:bg-gray-50 transition-colors"
      >
        {checklist && <SetupProgressRing value={checklist.completion} size={28} stroke={3} />}
        <span className="text-sm font-bold">{wizardLabel}</span>
        <span className="text-xs text-gray-400">{Math.round((checklist?.completion ?? 0) * 100)}%</span>
        <ChevronRight className="w-4 h-4 text-gray-400" />
      </button>
    );
  }

  const visibleSteps = isPatientMode
    ? (checklist?.steps ?? []).filter((s) => s.entity === 'patient')
    : (checklist?.steps ?? []).filter((s) => !s.entity);
  const allComplete = (checklist?.completion ?? 0) >= 1.0;

  const exitWizard = () => {
    setOpen(false);
    setCollapsed(false);
    setWizardActive(false);
    setEntity(null, null);
  };

  const getStepRoute = (step: SetupStep): string | null => {
    if (step.payload_hint?.route) return step.payload_hint.route;
    if (step.payload_hint?.sub_steps?.length) {
      const next = step.payload_hint.sub_steps.find((s) => !s.done);
      return (next ?? step.payload_hint.sub_steps[0])?.route ?? null;
    }
    return null;
  };

  return (
    <>
      <div
        className="fixed inset-0 z-[550] animate-in fade-in duration-200"
        onClick={() => setCollapsed(true)}
      />
      <aside
        className="fixed top-0 right-0 h-screen w-full sm:w-[400px] bg-white dark:bg-dark-bg z-[560] shadow-[-20px_0_50px_rgba(0,0,0,0.1)] border-l border-gray-100 dark:border-dark-border flex flex-col animate-in slide-in-from-right duration-300 safe-top safe-bottom"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header — context label + minimize */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 dark:border-dark-border">
          <div className="flex items-center gap-2">
            {checklist && <SetupProgressRing value={checklist.completion} size={36} stroke={4} />}
            <div>
              <h2 className="text-sm font-bold text-brand-navy dark:text-dark-text">{wizardLabel}</h2>
              <p className="text-[11px] text-gray-400 dark:text-dark-muted">
                {loading ? t('common.loading', 'Loading…') : `${Math.round((checklist?.completion ?? 0) * 100)}% · ${visibleSteps.filter(s => s.completed).length}/${visibleSteps.length}`}
              </p>
            </div>
          </div>
          <button
            onClick={() => setCollapsed(true)}
            title={t('setup.minimize', 'Minimize')}
            className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-dark-border text-gray-400"
          >
            <PanelRightClose className="w-4 h-4" />
          </button>
        </div>

        {/* Accordion step cards */}
        <div className="flex-1 overflow-y-auto px-3 py-3 space-y-2">
          {visibleSteps.map((step) => (
            <CollapsibleStepCard
              key={step.id}
              step={step}
              title={t(step.title_i18n_key, step.id)}
              description={t(`setup.step_desc.${step.id}`, '')}
              isExpanded={step.id === expandedStepId}
              onToggle={() => setExpandedStepId(step.id === expandedStepId ? null : step.id)}
              loading={loading}
              onRecheck={refresh}
              onOpen={() => {
                const route = getStepRoute(step);
                if (route) {
                  navigate(route);
                } else if (step.kind === 'inline_form') {
                  setOpen(false);
                  navigate(fullWizardRoute);
                }
              }}
            />
          ))}
          {allComplete && (
            <div className="rounded-xl p-4 text-center bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400">
              <CheckCircle2 className="w-6 h-6 mx-auto mb-1" />
              <p className="text-sm font-bold">{t('setup.all_complete')}</p>
            </div>
          )}
        </div>

        {/* Bottom: open full wizard + exit */}
        <div className="border-t border-gray-100 dark:border-dark-border flex">
          <button
            onClick={() => { setOpen(false); setCollapsed(false); navigate(fullWizardRoute); }}
            className="flex-1 px-4 py-2.5 text-xs font-semibold text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 flex items-center justify-center gap-1.5 transition-colors"
          >
            <Maximize2 className="w-3.5 h-3.5" />
            {t('setup.open_full_wizard', 'Open full wizard')}
          </button>
          <div className="w-px bg-gray-100 dark:bg-dark-border" />
          <button
            onClick={exitWizard}
            className="flex-1 px-4 py-2.5 text-xs font-semibold text-gray-500 dark:text-dark-muted hover:bg-gray-50 dark:hover:bg-dark-border/50 flex items-center justify-center gap-1.5 transition-colors"
          >
            <LogOut className="w-3.5 h-3.5" />
            {t('setup.exit_wizard', 'Exit wizard')}
          </button>
        </div>
      </aside>
    </>
  );
};

const CollapsibleStepCard: React.FC<{
  step: SetupStep;
  title: string;
  description: string;
  isExpanded: boolean;
  onToggle: () => void;
  loading: boolean;
  onRecheck: () => void;
  onOpen: () => void;
}> = ({ step, title, description, isExpanded, onToggle, loading, onRecheck, onOpen }) => {
  const { t } = useTranslation();
  const subSteps = step.payload_hint?.sub_steps;
  const subDone = subSteps?.filter((s) => s.done).length ?? 0;
  const subTotal = subSteps?.length ?? 0;
  const hasAction = step.kind !== 'derived' || step.payload_hint?.route;

  return (
    <div
      className={`rounded-xl border transition-all overflow-hidden ${
        isExpanded ? 'border-blue-200 dark:border-blue-800/50 shadow-sm' : 'border-gray-100 dark:border-dark-border'
      }`}
    >
      <button
        onClick={onToggle}
        className={`w-full flex items-center gap-2.5 px-3 py-2.5 text-left transition-colors ${
          isExpanded ? 'bg-blue-50/50 dark:bg-blue-900/10' : 'hover:bg-gray-50 dark:hover:bg-dark-border/30'
        }`}
      >
        {step.completed ? (
          <CheckCircle2 className="w-4 h-4 text-green-500 shrink-0" />
        ) : step.optional ? (
          <Circle className="w-4 h-4 text-gray-300 dark:text-dark-muted/50 shrink-0" />
        ) : (
          <Circle className="w-4 h-4 text-blue-400 shrink-0" fill="currentColor" />
        )}
        <div className="flex-1 min-w-0">
          <p className={`text-xs truncate ${step.completed ? 'text-gray-400 dark:text-dark-muted' : 'text-gray-800 dark:text-dark-text font-semibold'}`}>
            {title}
          </p>
          {!isExpanded && subTotal > 0 && (
            <p className="text-[10px] text-gray-400">{subDone}/{subTotal} done</p>
          )}
          {!isExpanded && step.optional && subTotal === 0 && (
            <p className="text-[10px] text-gray-400">{t('setup.optional', 'optional')}</p>
          )}
        </div>
        <ChevronDown className={`w-4 h-4 text-gray-400 shrink-0 transition-transform ${isExpanded ? 'rotate-180' : ''}`} />
      </button>

      {isExpanded && (
        <div className="px-3 pb-3 pt-1 space-y-3">
          {description && (
            <div className="rounded-lg bg-gray-50 dark:bg-dark-bg/50 px-3 py-2">
              <p className="text-[11px] text-gray-600 dark:text-dark-muted leading-relaxed">{description}</p>
            </div>
          )}

          {subTotal > 0 && (
            <div className="flex items-center gap-2">
              {subSteps!.map((sub) => (
                <div key={sub.id} className="flex items-center gap-1">
                  {sub.done ? (
                    <CheckCircle2 className="w-3 h-3 text-green-500" />
                  ) : (
                    <Circle className="w-3 h-3 text-gray-300 dark:text-dark-muted/50" fill="currentColor" />
                  )}
                  <span className={`text-[10px] ${sub.done ? 'text-gray-400' : 'text-gray-600 dark:text-dark-muted font-medium'}`}>
                    {t(`setup.subwizard.ai.${sub.id}`, sub.id)}
                  </span>
                </div>
              ))}
            </div>
          )}

          {hasAction && (
            <div className="flex items-center gap-2">
              <button
                onClick={onRecheck}
                disabled={loading}
                className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] font-semibold rounded-lg bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border text-gray-600 dark:text-dark-muted hover:bg-gray-50 disabled:opacity-50"
              >
                <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
                {t('setup.recheck', 'Recheck')}
              </button>
              <button
                onClick={onOpen}
                className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] font-semibold rounded-lg bg-blue-600 text-white hover:bg-blue-700"
              >
                <ExternalLink className="w-3 h-3" />
                {step.completed ? t('setup.action_manage', 'Manage') : t('setup.action_open', 'Open')}
              </button>
            </div>
          )}

          {!hasAction && step.kind === 'derived' && (
            <p className="text-[11px] text-gray-400 italic px-1">
              {step.completed
                ? t('setup.redirect_hint_complete', 'Completed.')
                : t('setup.derived_hint', 'This step is checked automatically.')}
            </p>
          )}
        </div>
      )}
    </div>
  );
};

export default SetupWizardDrawer;
