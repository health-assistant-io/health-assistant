import React from 'react';
import { PageHeader, type BreadcrumbItem } from '../ui/PageHeader';
import { SetupProgressRing } from './SetupProgressRing';
import { Stepper, type StepperStepView } from './Stepper';

interface SetupLayoutProps {
  /** Localised wizard title (e.g. "Patient setup"). */
  title: string;
  breadcrumbs?: BreadcrumbItem[];
  /** Steps for the left pane (titles already localised). */
  steps: StepperStepView[];
  activeStepId: string;
  onSelectStep: (stepId: string) => void;
  /** Optional group label over the stepper (e.g. "Patient profile"). */
  groupLabel?: string;
  /** Mandatory-completion ratio (0–1) for the header ring. */
  completion: number;
  /** The active step's panel (right pane). */
  children: React.ReactNode;
  /** Footer actions (e.g. Previous / Next / Finish). */
  footer?: React.ReactNode;
}

/**
 * Two-pane wizard shell shared by the patient setup wizard and the role
 * wizard. Left: a `Stepper` + completion ring. Right: the active step
 * panel (passed as children). Responsive: collapses to a single pane on
 * mobile with a back button.
 *
 * The shell owns no data; the parent wizard page fetches the checklist +
 * patient, manages active-step state, and renders the step panel via
 * `StepRenderer`. This keeps the shell reusable across entities (patient /
 * doctor / organization / role).
 */
export const SetupLayout: React.FC<SetupLayoutProps> = ({
  title,
  breadcrumbs,
  steps,
  activeStepId,
  onSelectStep,
  groupLabel,
  completion,
  children,
  footer,
}) => {
  return (
    <>
      <PageHeader title={title} breadcrumbs={breadcrumbs} />

      <div className="max-w-6xl mx-auto px-4 pb-16">
        <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-6">
          {/* Left pane: progress + stepper (sticky on desktop) */}
          <aside className="lg:sticky lg:top-24 lg:self-start space-y-4">
            <div className="rounded-2xl bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border p-4 shadow-sm">
              <div className="flex items-center gap-3">
                <SetupProgressRing value={completion} size={56} />
                <div>
                  <p className="text-sm font-semibold text-brand-navy dark:text-dark-text">
                    {Math.round(completion * 100)}% complete
                  </p>
                  <p className="text-xs text-gray-400 dark:text-dark-muted">required steps</p>
                </div>
              </div>
            </div>
            <div className="rounded-2xl bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border p-3 shadow-sm">
              <Stepper
                steps={steps}
                activeStepId={activeStepId}
                onSelect={onSelectStep}
                groupLabel={groupLabel}
              />
            </div>
          </aside>

          {/* Right pane: active step panel */}
          <main className="rounded-2xl bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border p-6 shadow-sm min-h-[400px]">
            {children}
            {footer && (
              <div className="mt-8 pt-6 border-t border-gray-100 dark:border-dark-border flex items-center justify-between gap-3">
                {footer}
              </div>
            )}
          </main>
        </div>
      </div>
    </>
  );
};

export default SetupLayout;
