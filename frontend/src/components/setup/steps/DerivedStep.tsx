import React from 'react';
import { CheckCircle2, Info } from 'lucide-react';
import type { SetupStep } from '../../../types/setup';

interface DerivedStepProps {
  step: SetupStep;
  /** Localised title (parent translates `title_i18n_key`). */
  title: string;
  /** Localised explanation shown under the title. */
  description?: string;
}

/**
 * Renderer for `kind = "derived"` steps: read-only evaluation. The wizard
 * shows the completion state + a hint — there is nothing for the user to
 * fill in here; the step flips green when the underlying data exists
 * (e.g. "system catalog seeded").
 */
export const DerivedStep: React.FC<DerivedStepProps> = ({ step, title, description }) => {
  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3">
        {step.completed ? (
          <CheckCircle2 className="w-6 h-6 text-green-500 shrink-0 mt-0.5" />
        ) : (
          <Info className="w-6 h-6 text-blue-500 shrink-0 mt-0.5" />
        )}
        <div>
          <h3 className="text-lg font-bold text-brand-navy dark:text-dark-text">{title}</h3>
          {step.optional && (
            <span className="inline-block mt-1 text-[11px] font-medium uppercase tracking-wide text-gray-400 dark:text-dark-muted">
              optional
            </span>
          )}
        </div>
      </div>
      {description && (
        <p className="text-sm text-gray-600 dark:text-dark-muted leading-relaxed">{description}</p>
      )}
      <div
        className={`rounded-xl p-4 text-sm ${
          step.completed
            ? 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-300'
            : 'bg-blue-50 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300'
        }`}
      >
        {step.completed
          ? 'This step is complete.'
          : 'This step is checked automatically — no action needed here.'}
      </div>
    </div>
  );
};

export default DerivedStep;
