import React, { useState } from 'react';
import { Info } from 'lucide-react';

interface InfoTooltipProps {
  content: React.ReactNode;
  icon?: React.ReactNode;
  className?: string;
  position?: 'top' | 'bottom' | 'left' | 'right';
}

export const InfoTooltip: React.FC<InfoTooltipProps> = ({ 
  content, 
  icon = <Info className="w-4 h-4" />,
  className = '',
  position = 'top'
}) => {
  const [isVisible, setIsVisible] = useState(false);

  const positionClasses = {
    top: 'bottom-full mb-2 left-1/2 -translate-x-1/2',
    bottom: 'top-full mt-2 left-1/2 -translate-x-1/2',
    left: 'right-full mr-2 top-1/2 -translate-y-1/2',
    right: 'left-full ml-2 top-1/2 -translate-y-1/2',
  };

  return (
    <div 
      className={`relative inline-flex ${className}`}
      onMouseEnter={() => setIsVisible(true)}
      onMouseLeave={() => setIsVisible(false)}
      onFocus={() => setIsVisible(true)}
      onBlur={() => setIsVisible(false)}
    >
      <div className="cursor-help text-gray-400 hover:text-blue-500 transition-colors">
        {icon}
      </div>
      
      {isVisible && (
        <div 
          className={`absolute z-[100] w-64 p-3 bg-gray-900 dark:bg-gray-800 text-white text-xs font-medium rounded-xl shadow-xl animate-in fade-in zoom-in-95 duration-200 pointer-events-none ${positionClasses[position]}`}
        >
          {content}
        </div>
      )}
    </div>
  );
};
