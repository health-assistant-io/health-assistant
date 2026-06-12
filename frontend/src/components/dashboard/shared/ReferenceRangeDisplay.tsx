import React from 'react';

interface ReferenceRangeDisplayProps {
  displayText: string;
  className?: string;
  compact?: boolean;
}

export const ReferenceRangeDisplay: React.FC<ReferenceRangeDisplayProps> = ({ 
  displayText, 
  className = '',
  compact = false
}) => {
  if (!displayText || displayText === '--') return null;

  return (
    <span className={`font-mono font-black text-blue-600/80 dark:text-blue-400/80 bg-blue-50/50 dark:bg-blue-900/10 px-2 py-0.5 rounded-md ${compact ? 'text-[9px]' : 'text-[10px]'} ${className}`}>
      {displayText}
    </span>
  );
};
