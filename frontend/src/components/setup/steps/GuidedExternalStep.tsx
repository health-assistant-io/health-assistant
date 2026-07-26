import React from 'react';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, Circle, ExternalLink } from 'lucide-react';
import type { SetupStep } from '../../../types/setup';

interface GuidedExternalStepProps {
  step: SetupStep;
  /** Localised title. */
  title: string;
  /** Localised description. */
  description?: string;
  /** Navigate to a route (opens the real settings page + keeps the drawer open). */
  onNavigate?: (route: string) => void;
}

/**
 * Renderer for `external_config` / `redirect` steps that carry
 * `payload_hint.sub_steps` — a guided multi-step redirect that opens the
 * real settings page at the right tab for each sub-step, WITHOUT
 * duplicating the settings forms inline.
 *
 * Used by the AI config step: shows 3 cards (provider → model → tasks),
 * each with a done/todo status + an "Open" button that navigates to the
 * AIConfig page at the right `?tab=`. The wizard drawer stays open so the
 * user can recheck + move to the next sub-step.
 */
export const GuidedExternalStep: React.FC<GuidedExternalStepProps> = ({
  step,
  title,
  description,
  onNavigate,
}) => {
  const { t } = useTranslation();
  const subSteps = step.payload_hint?.sub_steps ?? [];

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
        <div>
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

      {/* Guided sub-step cards */}
      <div className="space-y-2">
        {subSteps.map((sub) => {
          const label = t(`setup.subwizard.ai.${sub.id}`, sub.id);
          return (
            <div
              key={sub.id}
              className={`flex items-center gap-3 p-3 rounded-xl border transition-colors ${
                sub.done
                  ? 'border-green-200 dark:border-green-800/50 bg-green-50/50 dark:bg-green-900/10'
                  : 'border-gray-200 dark:border-dark-border bg-gray-50/50 dark:bg-dark-bg/30'
              }`}
            >
              {sub.done ? (
                <CheckCircle2 className="w-5 h-5 text-green-500 shrink-0" />
              ) : (
                <Circle className="w-5 h-5 text-blue-400 shrink-0" fill="currentColor" />
              )}
              <div className="flex-1 min-w-0">
                <p className={`text-sm font-semibold ${sub.done ? 'text-gray-500 dark:text-dark-muted' : 'text-gray-800 dark:text-dark-text'}`}>
                  {label}
                </p>
                <p className="text-[11px] text-gray-400 dark:text-dark-muted/70">
                  {sub.done
                    ? t('setup.redirect_hint_complete', 'Completed — you can review or change these settings anytime.')
                    : t('setup.subwizard.ai.open_hint', 'Open the settings page to configure this.')}
                </p>
              </div>
              {sub.route && onNavigate && (
                <button
                  type="button"
                  onClick={() => onNavigate(sub.route)}
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg shrink-0 ${
                    sub.done
                      ? 'bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border text-gray-600 dark:text-dark-muted hover:bg-gray-50'
                      : 'bg-blue-600 text-white hover:bg-blue-700'
                  }`}
                >
                  <ExternalLink className="w-3.5 h-3.5" />
                  {sub.done ? t('setup.action_manage', 'Manage') : t('setup.action_open', 'Open')}
                </button>
              )}
            </div>
          );
        })}
      </div>

      {step.completed && (
        <div className="rounded-xl p-3 text-sm bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-300">
          {t('setup.redirect_hint_complete', 'Completed — you can review or change these settings anytime.')}
        </div>
      )}
    </div>
  );
};

export default GuidedExternalStep;
