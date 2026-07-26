import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, ChevronRight, ChevronLeft } from 'lucide-react';

export interface SubWizardStep {
  id: string;
  label: string;
  done: boolean;
  optional?: boolean;
}

interface SubWizardShellProps {
  title: string;
  description?: string;
  steps: SubWizardStep[];
  /** Index of the initially active sub-step (defaults to first not-done). */
  initialStep?: number;
  /** Called when the user finishes all sub-steps. */
  onComplete?: () => void;
  /** Complete label for the last step. */
  completeLabel?: string;
  children: (activeStepIndex: number) => React.ReactNode;
}

/**
 * Shared shell for an inline multi-step sub-wizard rendered inside the main
 * setup wizard panel (no navigation away). Used by PatientCreationWizard.
 *
 * Shows a compact horizontal step indicator (dots with done/active states)
 * + the active step's content + Previous/Next buttons. The caller provides
 * the step definitions (label + done state) and the content per index via
 * the children render-prop.
 */
export const SubWizardShell: React.FC<SubWizardShellProps> = ({
  title,
  description,
  steps,
  initialStep,
  onComplete,
  completeLabel,
  children,
}) => {
  const { t } = useTranslation();
  const firstNotDone = initialStep ?? steps.findIndex((s) => !s.done && !s.optional);
  const [activeIdx, setActiveIdx] = useState(Math.max(0, firstNotDone));

  const allDone = steps.every((s) => s.done || s.optional);
  const isLast = activeIdx >= steps.length - 1;

  const goNext = () => {
    if (isLast) {
      onComplete?.();
      return;
    }
    // Skip to the next not-done step if current is done.
    const nextIdx = activeIdx + 1;
    setActiveIdx(nextIdx);
  };
  const goPrevious = () => {
    if (activeIdx > 0) setActiveIdx(activeIdx - 1);
  };

  return (
    <div className="space-y-5">
      {/* Header */}
      <div>
        <h3 className="text-lg font-bold text-brand-navy dark:text-dark-text">{title}</h3>
        {description && (
          <p className="mt-1 text-sm text-gray-500 dark:text-dark-muted">{description}</p>
        )}
      </div>

      {/* Step indicator */}
      <div className="flex items-center gap-1">
        {steps.map((step, idx) => {
          const isDone = step.done;
          const isActive = idx === activeIdx;
          const isPast = idx < activeIdx;
          return (
            <React.Fragment key={step.id}>
              {idx > 0 && (
                <div className={`flex-1 h-0.5 rounded-full ${isPast || isDone ? 'bg-green-400' : 'bg-gray-200 dark:bg-dark-border'}`} />
              )}
              <button
                type="button"
                onClick={() => setActiveIdx(idx)}
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold transition-colors whitespace-nowrap ${
                  isActive
                    ? 'bg-blue-600 text-white'
                    : isDone
                      ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300'
                      : 'bg-gray-100 text-gray-500 dark:bg-dark-border dark:text-dark-muted'
                }`}
              >
                {isDone ? (
                  <Check className="w-3 h-3" strokeWidth={3} />
                ) : (
                  <span className="w-3.5 h-3.5 rounded-full border-2 border-current flex items-center justify-center text-[8px]">
                    {idx + 1}
                  </span>
                )}
                <span className="hidden sm:inline">{step.label}</span>
              </button>
            </React.Fragment>
          );
        })}
      </div>

      {/* Active step content */}
      <div className="rounded-2xl border border-gray-100 dark:border-dark-border bg-gray-50/50 dark:bg-dark-bg/30 p-5">
        {children(activeIdx)}
      </div>

      {/* Footer nav */}
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={goPrevious}
          disabled={activeIdx <= 0}
          className="inline-flex items-center gap-1 px-3 py-2 text-sm font-semibold text-gray-500 hover:text-gray-700 disabled:opacity-30"
        >
          <ChevronLeft className="w-4 h-4" /> {t('setup.previous')}
        </button>
        {allDone ? (
          <button
            type="button"
            onClick={() => onComplete?.()}
            className="inline-flex items-center gap-1.5 px-4 py-2 bg-green-600 text-white rounded-xl text-sm font-semibold hover:bg-green-700"
          >
            <Check className="w-4 h-4" /> {completeLabel ?? t('setup.finish')}
          </button>
        ) : (
          <button
            type="button"
            onClick={goNext}
            className="inline-flex items-center gap-1 px-4 py-2 bg-blue-600 text-white rounded-xl text-sm font-semibold hover:bg-blue-700"
          >
            {isLast ? (completeLabel ?? t('setup.finish')) : t('setup.next')}
            {!isLast && <ChevronRight className="w-4 h-4" />}
          </button>
        )}
      </div>
    </div>
  );
};

export default SubWizardShell;
