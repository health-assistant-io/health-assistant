import React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useDicomFrames } from '../../hooks/useDicomFrames';

interface Props {
  documentId: string;
  className?: string;
  onPageChange?: (page: number, total: number) => void;
}

export const AuthenticatedDicomPreview: React.FC<Props> = ({ documentId, className, onPageChange }) => {
  const { 
    currentPage, 
    totalPages, 
    currentUrl, 
    isLoading, 
    nextFrame, 
    prevFrame 
  } = useDicomFrames({ documentId });

  // Update parent if needed
  React.useEffect(() => {
    if (onPageChange && currentUrl) {
      onPageChange(currentPage, totalPages);
    }
  }, [currentPage, totalPages, currentUrl, onPageChange]);

  return (
    <div className="relative group flex flex-col items-center justify-center w-full h-full min-h-[400px]">
      {currentUrl ? (
        <img 
          src={currentUrl} 
          className={`${className} transition-opacity duration-300 ${isLoading ? 'opacity-50' : 'opacity-100'}`} 
          alt="DICOM Frame" 
        />
      ) : (
         <div className="animate-pulse bg-gray-800 w-full h-full rounded-xl flex items-center justify-center">
            <div className="w-10 h-10 border-t-2 border-blue-500 rounded-full animate-spin"></div>
         </div>
      )}
      
      {totalPages > 1 && (
        <div className="absolute inset-x-0 bottom-6 flex items-center justify-center gap-4 z-20">
          <div className="flex items-center gap-3 bg-black/80 backdrop-blur-xl px-4 py-2 rounded-2xl border border-white/10 text-white shadow-2xl scale-90 sm:scale-100 transition-transform group-hover:scale-105">
             <button 
               type="button"
               onClick={(e) => { e.stopPropagation(); prevFrame(); }}
               disabled={currentPage === 0 || isLoading}
               className="p-1.5 hover:bg-white/10 rounded-lg disabled:opacity-20 transition-colors"
             >
               <ChevronLeft className="w-5 h-5" />
             </button>
             
             <div className="flex flex-col items-center min-w-[80px]">
                <span className="text-[8px] font-black text-blue-400 uppercase tracking-widest mb-0.5">Frame</span>
                <span className="text-sm font-black tabular-nums">
                  {currentPage + 1} <span className="text-gray-500 font-medium mx-1">/</span> {totalPages}
                </span>
             </div>

             <button 
               type="button"
               onClick={(e) => { e.stopPropagation(); nextFrame(); }}
               disabled={currentPage === totalPages - 1 || isLoading}
               className="p-1.5 hover:bg-white/10 rounded-lg disabled:opacity-20 transition-colors"
             >
               <ChevronRight className="w-5 h-5" />
             </button>
          </div>
        </div>
      )}
      
      {isLoading && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/10 backdrop-blur-[1px] pointer-events-none rounded-xl">
           <div className="w-10 h-10 border-t-2 border-blue-500 border-r-2 border-r-transparent rounded-full animate-spin"></div>
        </div>
      )}
    </div>
  );
};
