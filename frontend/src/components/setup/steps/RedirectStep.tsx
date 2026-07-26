import React from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowRight, ExternalLink, CheckCircle2, Settings2 } from 'lucide-react';
import type { SetupStep } from '../../../types/setup';

interface RedirectStepProps {
  step: SetupStep;
  /** Localised title. */
  title: string;
  /** Localised explanation of what to do on the target page. */
  description?: string;
  /** Navigate to a route (in-app). */
  onNavigate?: (route: string) => void;
}

/**
 * Renderer for `kind = "redirect"` and `kind = "external_config"` steps.
 *
 * Both are a deep-link CTA to another route:
 * - `redirect` → an in-app page (e.g. "Create First Patient" → `/patients?new=patient`)
 * - `external_config` → a settings sub-page (e.g. AI config → `/admin/tenant/ai-config`)
 *
 * The CTA is **always available** — when the step is incomplete it reads
 * "Open" (go complete it); when complete it reads "Manage" (review or change
 * the settings). On click the wizard navigates away; when the user returns,
 * the parent re-polls the checklist so the step flips green in-place
 * (design D2 — no local "completed" state).
 */
export const RedirectStep: React.FC<RedirectStepProps> = ({
  step,
  title,
  description,
  onNavigate,
}) => {
  const { t } = useTranslation();
  const route = step.payload_hint?.route;
  const isExternal = step.kind === 'external_config';
  const label = step.completed
    ? t('setup.action_manage', 'Manage')
    : t('setup.action_open', 'Open');

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3">
        {step.completed ? (
          <CheckCircle2 className="w-6 h-6 text-green-500 shrink-0 mt-0.5" />
        ) : (
          <div className="w-6 h-6 rounded-full border-2 border-blue-300 bg-blue-50 dark:border-blue-700 dark:bg-blue-900/30 shrink-0 mt-0.5 flex items-center justify-center">
            <span className="w-2 h-2 rounded-full bg-blue-500" />
          </div>
        )}
        <div className="flex-1">
          <h3 className="text-lg font-bold text-brand-navy dark:text-dark-text">{title}</h3>
          {step.optional && (
            <span className="inline-block mt-1 text-[11px] font-medium uppercase tracking-wide text-gray-400 dark:text-dark-muted">
              {t('setup.optional', 'optional')}
            </span>
          )}
        </div>
      </div>

      {description && (
        <p className="text-sm text-gray-600 dark:text-dark-muted leading-relaxed">{description}</p>
      )}

      {step.completed ? (
        <div className="rounded-xl p-4 text-sm bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-300">
          {t('setup.redirect_hint_complete', 'Completed — you can review or change these settings anytime.')}
        </div>
      ) : (
        <div className="rounded-xl p-4 text-sm bg-blue-50 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300">
          {t('setup.redirect_hint_incomplete', 'Complete this step on the target page — it will be detected here automatically.')}
        </div>
      )}

      {route && onNavigate && (
        <button
          type="button"
          onClick={() => onNavigate(route)}
          className={`inline-flex items-center gap-2 px-4 py-2.5 rounded-xl font-semibold text-sm transition-colors shadow-sm dark:shadow-none ${
            step.completed
              ? 'bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border text-gray-700 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-border/50'
              : 'bg-blue-600 text-white hover:bg-blue-700 shadow-blue-200'
          }`}
        >
          {step.completed ? (
            <Settings2 className="w-4 h-4" />
          ) : isExternal ? (
            <ExternalLink className="w-4 h-4" />
          ) : (
            <ArrowRight className="w-4 h-4" />
          )}
          {label}
        </button>
      )}
    </div>
  );
};

export default RedirectStep;
