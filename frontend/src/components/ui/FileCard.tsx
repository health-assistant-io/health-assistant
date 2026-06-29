import React, { useState, useEffect } from 'react';
import { 
  FileText, X, Hospital, Eye, GripVertical, Sparkles 
} from 'lucide-react';

interface FileCardProps {
  file: File;
  onRemove: () => void;
  onPreview: () => void;
  onToggleInclusion: () => void;
  includeInExtraction: boolean;
  draggable?: boolean;
  onDragStart?: (e: React.DragEvent) => void;
}

export const FileCard: React.FC<FileCardProps> = ({ 
  file, onRemove, onPreview, onToggleInclusion, includeInExtraction, 
  draggable = false, onDragStart 
}) => {
  const isDicom = file.name.toLowerCase().endsWith('.dcm');
  const isImage = file.type.startsWith('image/') && !isDicom;
  const isPdf = file.type === 'application/pdf';
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  useEffect(() => {
    if (isImage) {
      const url = URL.createObjectURL(file);
      setPreviewUrl(url);
      return () => URL.revokeObjectURL(url);
    }
  }, [file, isImage]);
  
  return (
    <div 
      draggable={draggable}
      onDragStart={onDragStart}
      onClick={(e) => { e.stopPropagation(); onPreview(); }}
      className="group relative w-40 h-40 bg-white dark:bg-dark-surface rounded-2xl border border-gray-100 dark:border-dark-border shadow-sm hover:shadow-xl transition-all hover:-translate-y-1 cursor-pointer overflow-hidden"
    >
      <div className="h-24 w-full bg-gray-50 dark:bg-dark-bg flex items-center justify-center relative overflow-hidden">
        {isImage && previewUrl ? (
          <img 
            src={previewUrl} 
            className="w-full h-full object-cover transition-transform group-hover:scale-110" 
            alt={file.name} 
          />
        ) : isDicom ? (
          <div className="flex flex-col items-center">
            <div className="p-2 bg-indigo-50 dark:bg-indigo-900/20 rounded-lg">
              <Hospital className="w-8 h-8 text-indigo-500" />
            </div>
            <span className="text-[8px] font-black text-indigo-500 mt-1 uppercase tracking-tighter">DICOM</span>
          </div>
        ) : isPdf ? (
          <div className="flex flex-col items-center">
            <div className="p-2 bg-red-50 dark:bg-red-900/20 rounded-lg">
              <FileText className="w-8 h-8 text-red-500" />
            </div>
            <span className="text-[8px] font-black text-red-500 mt-1 uppercase tracking-tighter">PDF</span>
          </div>
        ) : (
          <div className="flex flex-col items-center">
            <div className="p-2 bg-gray-100 dark:bg-dark-border rounded-lg">
               <FileText className="w-8 h-8 text-gray-400" />
            </div>
            <span className="text-[8px] font-black text-gray-400 mt-1 uppercase tracking-tighter">FILE</span>
          </div>
        )}
        
        <button 
          type="button"
          onClick={(e) => { e.stopPropagation(); onRemove(); }}
          className="absolute top-1.5 right-1.5 p-1 bg-white/80 dark:bg-dark-surface/80 backdrop-blur-md rounded-full text-gray-400 hover:text-red-500 hover:bg-white dark:hover:bg-dark-surface opacity-0 group-hover:opacity-100 transition-all shadow-sm z-10"
          title="Remove document"
        >
          <X className="w-3 h-3" />
        </button>

        <div className="absolute inset-0 bg-black/5 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
          <div className="p-2 bg-white/90 dark:bg-dark-surface/90 rounded-full shadow-lg transform translate-y-4 group-hover:translate-y-0 transition-transform">
            <Eye className="w-4 h-4 text-blue-500" />
          </div>
        </div>
      </div>

      <div className="p-2 relative" onClick={(e) => e.stopPropagation()}>
        {draggable && (
            <div className="absolute top-1.5 left-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                <GripVertical className="w-3 h-3 text-gray-300" />
            </div>
        )}
        <p className="text-[10px] font-bold text-gray-700 dark:text-dark-text truncate pl-2" title={file.name}>
          {file.name}
        </p>
        <div className="flex items-center justify-between mt-1 pl-2">
          <span className="text-[8px] text-gray-400 font-medium uppercase">
            {(file.size / 1024).toFixed(0)} KB
          </span>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onToggleInclusion(); }}
            aria-pressed={includeInExtraction}
            aria-label={includeInExtraction ? 'Disable AI extraction for this document' : 'Enable AI extraction for this document'}
            title={includeInExtraction ? 'AI extraction enabled — click to disable' : 'AI extraction disabled — click to enable'}
            className={`flex items-center gap-1 px-2 py-1 rounded-lg transition-all border ${
              includeInExtraction
                ? 'bg-indigo-500/10 border-indigo-500/30 shadow-sm hover:bg-indigo-500/20'
                : 'bg-gray-500/5 border-transparent hover:bg-gray-500/10 hover:border-gray-300/30'
            }`}
          >
            <Sparkles className={`w-3 h-3 shrink-0 transition-colors ${includeInExtraction ? 'text-indigo-500' : 'text-gray-400'}`} />
            <span className={`text-[9px] font-black uppercase tracking-tighter transition-colors ${
              includeInExtraction
                ? 'bg-gradient-to-r from-indigo-600 via-purple-500 to-indigo-600 dark:from-indigo-400 dark:via-purple-400 dark:to-indigo-400 bg-clip-text text-transparent'
                : 'text-gray-400'
            }`}>
              {includeInExtraction ? 'AI Extract' : 'AI Off'}
            </span>
          </button>
        </div>
      </div>
    </div>
  );
};
