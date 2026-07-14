import React from 'react';
import { clsx } from 'clsx';

type Variant = 'ghost' | 'solid' | 'danger';
type Size = 'sm' | 'md' | 'lg';

interface IconButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** Accessible label — required for icon-only buttons. */
  'aria-label': string;
  variant?: Variant;
  /** Controls icon + target size. `md` enforces the 44px minimum. */
  size?: Size;
}

const VARIANT_STYLES: Record<Variant, string> = {
  ghost:
    'text-gray-400 hover:text-gray-600 hover:bg-gray-100 dark:text-dark-muted dark:hover:text-dark-text dark:hover:bg-dark-bg',
  solid:
    'bg-blue-600 text-white hover:bg-blue-700 shadow-sm',
  danger:
    'text-red-500 hover:text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-900/20',
};

const SIZE_STYLES: Record<Size, string> = {
  sm: 'w-8 h-8 [&>svg]:w-4 [&>svg]:h-4',
  md: 'w-11 h-11 [&>svg]:w-5 [&>svg]:h-5',
  lg: 'w-12 h-12 [&>svg]:w-6 [&>svg]:h-6',
};

/**
 * Icon-only button with enforced minimum touch targets.
 *
 * `size="md"` (default) guarantees a 44×44px hit area per Apple/Google
 * guidelines. Use `size="sm"` only in dense data rows where space is
 * critical — the target is 32px which is below recommendation.
 *
 * `aria-label` is mandatory.
 */
export const IconButton: React.FC<IconButtonProps> = ({
  variant = 'ghost',
  size = 'md',
  className,
  children,
  ...props
}) => {
  return (
    <button
      type="button"
      className={clsx(
        'inline-flex items-center justify-center rounded-xl transition-all active:scale-90 shrink-0 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 dark:focus-visible:ring-offset-dark-surface disabled:opacity-50 disabled:pointer-events-none',
        VARIANT_STYLES[variant],
        SIZE_STYLES[size],
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
};

export default IconButton;
