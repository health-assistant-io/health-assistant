/**
 * TabInfoButton — a modular "(i)" affordance that opens a rich popover
 * explaining what the active tab shows / what the user should expect.
 *
 * Built on {@link InfoTooltip}'s click-trigger mode (Portal-rendered so it
 * escapes card overflow clipping, dismissable by outside-click / Escape).
 * Intended for the active tab in a tabbed detail view — place it next to the
 * tab strip and feed it the current tab's title + description.
 */
import React from 'react';
import { Info } from 'lucide-react';
import { InfoTooltip } from './InfoTooltip';

interface TabInfoButtonProps {
  title: string;
  description: string;
  className?: string;
}

export const TabInfoButton: React.FC<TabInfoButtonProps> = ({
  title,
  description,
  className = '',
}) => {
  return (
    <InfoTooltip
      trigger="click"
      title={title}
      content={description}
      ariaLabel={`About ${title}`}
      icon={<Info className="w-4 h-4" />}
      className={className}
    />
  );
};
