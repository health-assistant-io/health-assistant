import React, { useEffect } from 'react';
import { X, ExternalLink } from 'lucide-react';

interface PdfViewerProps {
  url: string;
  filename: string;
  onClose: () => void;
}

export const PdfViewer: React.FC<PdfViewerProps> = ({ url, filename, onClose }) => {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-[1000] flex flex-col bg-black/95 backdrop-blur-sm">
      {/* Header */}
      <div className="flex items-center justify-between p-4 bg-black/40 text-white">
        <div className="flex items-center space-x-4 overflow-hidden">
          <h2 className="text-lg font-bold truncate">{filename}</h2>
          <span className="px-2 py-0.5 bg-blue-600 text-[10px] font-bold uppercase rounded">PDF View</span>
        </div>
        <div className="flex items-center space-x-3">
          <a 
            href={url} 
            target="_blank" 
            rel="noopener noreferrer"
            className="p-2 text-white/70 hover:text-white hover:bg-white/10 rounded-lg transition-all"
            title="Open in new tab"
          >
            <ExternalLink className="w-5 h-5" />
          </a>
          <button 
            onClick={onClose}
            className="p-2 text-white/70 hover:text-white hover:bg-red-500 rounded-full transition-all"
            title="Close (Esc)"
          >
            <X className="w-6 h-6" />
          </button>
        </div>
      </div>

      {/* PDF Content */}
      <div className="flex-1 w-full h-full p-4 sm:p-6 lg:p-8 flex justify-center">
        <div className="w-full max-w-5xl h-full bg-white rounded-xl shadow-2xl overflow-hidden">
          <iframe 
            src={`${url}#view=FitH`} 
            className="w-full h-full border-0"
            title="PDF Document Viewer"
          />
        </div>
      </div>
      
      {/* Footer Info */}
      <div className="p-3 text-center text-white/40 text-[10px] uppercase tracking-widest">
        Secure Medical Document Viewer • Escape to close
      </div>
    </div>
  );
};
