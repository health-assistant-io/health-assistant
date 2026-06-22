import React from 'react';
import { ArrowUpRight } from 'lucide-react';

interface Props {
  /** Click handler — typically navigates to the entity's main page. */
  onClick: () => void;
  /** Used for both the hover tooltip (`title`) and the screen-reader label (`aria-label`). */
  label: string;
  className?: string;
}

/**
 * Compact icon-only "open in full page" button used by summary cards.
 * Renders an ArrowUpRight glyph in a subtle gray pill that highlights on hover.
 * Intentionally text-free to avoid visual repetition across stacked cards.
 */
const OpenPageButton: React.FC<Props> = ({ onClick, label, className = '' }) => (
  <button
    type="button"
    onClick={onClick}
    title={label}
    aria-label={label}
    className={`p-1.5 rounded-lg text-gray-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 dark:hover:text-blue-400 transition-all shrink-0 ${className}`}
  >
    <ArrowUpRight className="w-4 h-4" />
  </button>
);

export default OpenPageButton;
