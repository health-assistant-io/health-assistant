import React from 'react';
import { Check, Circle, Lock } from 'lucide-react';

interface StepStatusBadgeProps {
  completed: boolean;
  optional: boolean;
  /** Render the compact (icon-only) variant for the stepper rows. */
  compact?: boolean;
}

/**
 * The status pill/icon on a setup step row.
 *
 * - completed → green check
 * - optional + incomplete → hollow circle (muted) — "you may skip this"
 * - mandatory + incomplete → blue circle — "still to do"
 *
 * Pure presentational.
 */
export const StepStatusBadge: React.FC<StepStatusBadgeProps> = ({
  completed,
  optional,
  compact = false,
}) => {
  if (completed) {
    return (
      <span
        className={`inline-flex items-center justify-center rounded-full bg-green-100 text-green-600 dark:bg-green-900/30 dark:text-green-400 ${
          compact ? 'w-5 h-5' : 'w-6 h-6'
        }`}
        aria-label="completed"
      >
        <Check className={compact ? 'w-3 h-3' : 'w-4 h-4'} strokeWidth={3} />
      </span>
    );
  }
  if (optional) {
    return (
      <span
        className={`inline-flex items-center justify-center rounded-full border border-gray-200 text-gray-300 dark:border-dark-border dark:text-dark-muted/60 ${
          compact ? 'w-5 h-5' : 'w-6 h-6'
        }`}
        aria-label="optional"
        title="optional"
      >
        <Circle className={compact ? 'w-2 h-2' : 'w-2.5 h-2.5'} fill="currentColor" />
      </span>
    );
  }
  return (
    <span
      className={`inline-flex items-center justify-center rounded-full border-2 border-blue-200 bg-blue-50 text-blue-500 dark:border-blue-900/50 dark:bg-blue-900/20 dark:text-blue-300 ${
        compact ? 'w-5 h-5' : 'w-6 h-6'
      }`}
      aria-label="to do"
    >
      {compact ? (
        <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
      ) : (
        <Lock className="w-3 h-3" />
      )}
    </span>
  );
};

export default StepStatusBadge;
