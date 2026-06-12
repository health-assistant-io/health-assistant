import React from 'react';
import { FileText } from 'lucide-react';
import { DicomViewer } from './DicomViewer';
import { ImageViewer } from './ImageViewer';
import { PdfViewer } from './PdfViewer';
import { TextViewer } from './TextViewer';

interface FilePreviewManagerProps {
  url: string;
  filename: string;
  type: string;
  onClose: () => void;
  isBackendProcessed?: boolean;
  localFile?: File;
}

export const FilePreviewManager: React.FC<FilePreviewManagerProps> = ({ 
  url, filename, type, onClose, isBackendProcessed = false, localFile 
}) => {
  if (filename.toLowerCase().endsWith('.dcm')) {
    return <DicomViewer url={url} filename={filename} onClose={onClose} isLocal={!isBackendProcessed} localFile={localFile} />;
  }
  if (type.startsWith('image/')) {
    return <ImageViewer url={url} filename={filename} onClose={onClose} />;
  }
  if (type === 'application/pdf') {
    return <PdfViewer url={url} filename={filename} onClose={onClose} />;
  }
  if (type.startsWith('text/') || filename.endsWith('.txt') || filename.endsWith('.md')) {
    return <TextViewer url={url} filename={filename} onClose={onClose} />;
  }
  
  return (
    <div className="fixed inset-0 z-[1000] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
       <div className="bg-white dark:bg-dark-surface rounded-2xl p-8 max-w-sm w-full text-center">
          <div className="w-16 h-16 bg-blue-50 dark:bg-blue-900/20 rounded-full flex items-center justify-center mx-auto mb-4">
             <FileText className="w-8 h-8 text-blue-500" />
          </div>
          <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text mb-2">Preview Not Available</h3>
          <p className="text-sm text-gray-500 dark:text-dark-muted mb-6">
            We can't preview this file type ({type}) yet, but you can still upload it.
          </p>
          <button 
            type="button"
            onClick={onClose}
            className="w-full py-2 bg-blue-600 text-white rounded-xl font-bold transition-all hover:bg-blue-700"
          >
            Close
          </button>
       </div>
    </div>
  );
};
