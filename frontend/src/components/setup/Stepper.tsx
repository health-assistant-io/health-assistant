import React from 'react';
import type { SetupStep } from '../../types/setup';
import { StepStatusBadge } from './StepStatusBadge';

/**
 * A setup step with its already-localised title, for the stepper view.
 * The parent translates `step.title_i18n_key` → `title`.
 */
export interface StepperStepView extends SetupStep {
  title: string;
}

interface StepperProps {
  steps: StepperStepView[];
  /** Currently active step id. */
  activeStepId: string;
  /** Select a step. */
  onSelect: (stepId: string) => void;
  /** Optional group label for the steps (e.g. "Patient profile" / "Your account"). */
  groupLabel?: string;
}

/**
 * Vertical step list for the wizard left pane.
 *
 * Renders each step's status badge + title + optional/mandatory hint, and
 * highlights the active step. Clicking a row selects it (the wizard shows
 * that step's panel on the right). Pure presentational — the parent owns
 * the active-step state + the step-title translation (via `t(step.title_i18n_key)`).
 *
 * The step `title` is passed already-localised by the parent so this
 * component stays i18n-free and unit-testable with plain strings.
 */
export const Stepper: React.FC<StepperProps> = ({
  steps,
  activeStepId,
  onSelect,
  groupLabel,
}) => {
  return (
    <nav className="space-y-1" aria-label="Setup steps">
      {groupLabel && (
        <p className="px-2 pb-2 text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-dark-muted">
          {groupLabel}
        </p>
      )}
      <ol className="space-y-1">
        {steps.map((step) => {
          const isActive = step.id === activeStepId;
          return (
            <li key={step.id}>
              <button
                type="button"
                onClick={() => onSelect(step.id)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-left transition-colors ${
                  isActive
                    ? 'bg-blue-50 dark:bg-blue-900/20 ring-1 ring-blue-200 dark:ring-blue-800/50'
                    : 'hover:bg-gray-50 dark:hover:bg-dark-border/50'
                }`}
                aria-current={isActive ? 'step' : undefined}
              >
                <StepStatusBadge completed={step.completed} optional={step.optional} compact />
                <span className="flex-1 min-w-0">
                  <span
                    className={`block text-sm font-medium truncate ${
                      isActive
                        ? 'text-blue-700 dark:text-blue-300'
                        : step.completed
                          ? 'text-gray-500 dark:text-dark-muted'
                          : 'text-gray-800 dark:text-dark-text'
                    }`}
                  >
                    {step.title}
                  </span>
                  {step.optional && !step.completed && (
                    <span className="block text-[11px] text-gray-400 dark:text-dark-muted/70">
                      optional
                    </span>
                  )}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
};

export default Stepper;
