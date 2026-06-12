import React from 'react';

interface Props {
  sidebar?: React.ReactNode;
  list: React.ReactNode; // Can be the raw content or entire column if listHeader is omitted
  listHeader?: React.ReactNode; // Optional header for the list column
  detail: React.ReactNode;
  listWidth?: string;
  className?: string;
  containerRef?: React.RefObject<HTMLDivElement>;
  showDetail?: boolean;
  withListStyling?: boolean; // Whether to apply the gray rounded background to the list column
}

export const MasterDetailLayout: React.FC<Props> = ({ 
  sidebar, 
  list, 
  listHeader,
  detail, 
  listWidth = "lg:w-96",
  className = "",
  containerRef,
  showDetail = true,
  withListStyling = true
}) => {
  return (
    <div ref={containerRef} className={`flex flex-1 gap-6 lg:gap-8 overflow-hidden min-h-0 ${className}`}>
      {/* Left Sidebar - Categories/Filters */}
      {sidebar && (
        <div className="hidden lg:flex w-56 flex-shrink-0 flex-col overflow-y-auto no-scrollbar min-h-0">
          {sidebar}
        </div>
      )}

      {/* Middle Column - Main List */}
      <div className={`w-full ${listWidth} flex-shrink-0 flex flex-col min-w-0 min-h-0`}>
        {withListStyling ? (
          <div className="flex flex-col h-full min-h-0 overflow-hidden bg-gray-50/50 dark:bg-dark-bg/50 rounded-[2rem]">
            {listHeader && (
              <div className="flex items-center justify-between mb-4 flex-shrink-0 px-6 pt-6">
                {listHeader}
              </div>
            )}
            <div className={`flex-1 overflow-y-auto px-6 pb-6 custom-scrollbar min-h-0 ${!listHeader ? 'pt-6' : ''}`}>
              {list}
            </div>
          </div>
        ) : (
          list
        )}
      </div>

      {/* Right Content - Preview/Detail */}
      {showDetail && (
        <div className="hidden lg:flex lg:flex-1 flex-col min-h-0 bg-white dark:bg-dark-surface rounded-[2rem] border border-gray-100 dark:border-dark-border shadow-2xl shadow-blue-900/5 overflow-hidden">
          {detail}
        </div>
      )}
    </div>
  );
};
