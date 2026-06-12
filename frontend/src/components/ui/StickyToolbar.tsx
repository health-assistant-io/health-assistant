import React from 'react';

interface StickyToolbarProps {
  children?: React.ReactNode;
  actions?: React.ReactNode;
  details?: React.ReactNode;
  center?: React.ReactNode;
  sticky?: boolean;
  className?: string;
}

export const StickyToolbar: React.FC<StickyToolbarProps> = ({ 
  children, 
  actions, 
  details, 
  center,
  sticky = true,
  className = ""
}) => {
  return (
    <div className={`
      ${sticky ? 'sticky top-[-6px] md:top-[-14px] lg:top-[-18px] z-[450] backdrop-blur-md bg-gray-50/90 dark:bg-dark-bg/90 py-3 mb-6' : 'py-2 mb-4'} 
      flex flex-wrap items-center justify-between gap-4 transition-all duration-300 border-b border-gray-200 dark:border-dark-border -mx-2 sm:-mx-4 md:-mx-6 lg:-mx-8 px-2 sm:px-4 md:px-6 lg:px-8
      ${className}
    `}>
      <div className="flex flex-wrap items-center gap-6 flex-1 min-w-0">
        {details && (
          <div className="flex items-center">
            {details}
          </div>
        )}
        
        {center && (
          <div className="flex-1 flex justify-center">
            {center}
          </div>
        )}

        {children}
      </div>

      {actions && (
        <div className="flex flex-wrap items-center gap-3 ml-auto">
          {actions}
        </div>
      )}
    </div>
  );
};

export default StickyToolbar;
