import React from 'react';
import { useNavigate } from 'react-router-dom';
import { ExternalLink } from 'lucide-react';

export interface CardTitleProps {
  /** Optional leading icon node (each card controls its own icon styling) */
  icon?: React.ReactNode;
  /** Title content to display */
  title: React.ReactNode;
  /** In-app route to navigate to when the title is clicked. When omitted, the title renders as plain (non-interactive) text. */
  to?: string;
  /** Optional node rendered beneath the title (e.g. unit / reference-range line) */
  subtitle?: React.ReactNode;
  /** Classes applied to the clickable wrapper (the icon + title + external-link row) */
  className?: string;
  /** Classes applied to the title element */
  titleClassName?: string;
  /** HTML tag used for the title (defaults to h3) */
  as?: 'h2' | 'h3' | 'h4';
}

/**
 * Generic dashboard card title that is clickable (navigates to `to` in-app)
 * and reveals an "open in new tab" affordance icon on hover. Clicking the
 * icon opens the same route in a new browser tab.
 *
 * Uses a scoped `group/title` so the hover state is isolated to the title and
 * does not conflict with the card-level `group` hover (e.g. remove button).
 *
 * Pass `to` only when navigation is appropriate (e.g. hide it in edit mode or
 * when the target entity is unknown); when omitted the title is static.
 */
export const CardTitle: React.FC<CardTitleProps> = ({
  icon,
  title,
  to,
  subtitle,
  className = '',
  titleClassName = '',
  as: Tag = 'h3',
}) => {
  const navigate = useNavigate();
  const clickable = !!to;

  const handleClick = (e: React.MouseEvent) => {
    if (!clickable) return;
    e.stopPropagation();
    navigate(to);
  };

  const handleNewTab = (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    window.open(to, '_blank', 'noopener,noreferrer');
  };

  return (
    <div
      role={clickable ? 'link' : undefined}
      onClick={clickable ? handleClick : undefined}
      title={clickable && typeof title === 'string' ? title : undefined}
      className={`group/title relative flex items-center space-x-2 ${clickable ? 'cursor-pointer hover:opacity-80 nodrag' : ''} ${className}`}
    >
      {icon}
      <div className="min-w-0">
        <div className="flex items-center space-x-1.5">
          <Tag className={titleClassName}>{title}</Tag>
          {clickable && (
            <ExternalLink
              onClick={handleNewTab}
              className="w-4 h-4 text-gray-400 dark:text-dark-muted opacity-0 group-hover/title:opacity-100 hover:!text-blue-500 transition-opacity shrink-0"
            />
          )}
        </div>
        {subtitle && <div className="mt-1">{subtitle}</div>}
      </div>
    </div>
  );
};
