import React from 'react';
import { clsx } from 'clsx';
import type { LucideIcon } from 'lucide-react';

interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
  /** Compact mode — less padding, for use inside cards/small containers. */
  compact?: boolean;
}

/**
 * Consistent empty-state presentation.
 *
 * Replaces the ~15 bespoke inline empty-state snippets across the codebase
 * (e.g. "No export jobs yet", "No providers configured", "Nothing here yet").
 */
export const EmptyState: React.FC<EmptyStateProps> = ({
  icon: Icon,
  title,
  description,
  action,
  className,
  compact = false,
}) => {
  return (
    <div
      className={clsx(
        'flex flex-col items-center justify-center text-center',
        compact ? 'py-6' : 'py-12',
        className,
      )}
    >
      {Icon && (
        <Icon
          className={clsx(
            'text-gray-200 dark:text-dark-border mb-3',
            compact ? 'w-8 h-8' : 'w-12 h-12',
          )}
          aria-hidden="true"
        />
      )}
      <p
        className={clsx(
          'font-bold text-gray-500 dark:text-dark-muted',
          compact ? 'text-sm' : 'text-base',
        )}
      >
        {title}
      </p>
      {description && (
        <p className="text-sm text-gray-400 dark:text-dark-muted mt-1 max-w-sm">
          {description}
        </p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
};

export default EmptyState;
