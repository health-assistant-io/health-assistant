import React from 'react';
import { useTranslation } from 'react-i18next';
import { ImageIcon, FileText, File } from 'lucide-react';
import { DynamicIcon } from '../ui/DynamicIcon';
import { AuthenticatedImage } from '../ui/AuthenticatedImage';
import { CardStyles } from '../../utils/cardStyles';

interface Props {
  doc: any;
  isSelected?: boolean;
  onClick?: () => void;
  viewMode?: 'grid' | 'list';
  category?: string;
  categoryDetails?: any;
  className?: string;
}

export const DocumentCard: React.FC<Props> = ({
  doc,
  isSelected = false,
  onClick,
  viewMode = 'list',
  category,
  categoryDetails,
  className = ''
}) => {
  const { t } = useTranslation();

  const getFileIcon = (filename: string) => {
    const ext = filename.split('.').pop()?.toLowerCase();
    if (ext?.match(/(png|jpe?g|webp|gif|bmp)$/)) return <ImageIcon className="w-5 h-5" />;
    if (ext === 'pdf') return <FileText className="w-5 h-5" />;
    if (ext?.match(/(txt|md)$/)) return <FileText className="w-5 h-5" />;
    return <File className="w-5 h-5" />;
  };

  const isImage = doc.filename.match(/\.(png|jpe?g|webp|gif|bmp|dcm)$/i);

  if (viewMode === 'grid') {
    return (
      <div 
        onClick={onClick}
        className={`${CardStyles.container(isSelected)} flex flex-col items-center text-center p-4 h-full flex-shrink-0 ${className}`}
      >
        <div className={`w-full aspect-square mb-3 rounded-xl overflow-hidden flex items-center justify-center flex-shrink-0 transition-colors
          ${doc.status === 'failed' ? 'bg-red-50 dark:bg-red-900/20 text-red-500' : 
            isSelected ? 'bg-blue-50 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400' : 'bg-gray-100 dark:bg-dark-bg text-gray-400 dark:text-dark-muted group-hover:bg-gray-200 dark:group-hover:bg-dark-border'}
        `}>
          {isImage ? (
            <AuthenticatedImage 
              documentId={doc.id} 
              className="w-full h-full object-cover opacity-90 group-hover:opacity-100 transition-opacity" 
            />
          ) : (
            getFileIcon(doc.filename)
          )}
        </div>
        
        <div className="overflow-hidden w-full flex-1 flex flex-col justify-center">
          <div className="flex items-center justify-center mb-1">
            <span 
              className="text-[9px] font-black uppercase tracking-[0.1em] truncate flex items-center gap-1"
              style={{ color: categoryDetails?.color || '#9ca3af' }}
            >
               {categoryDetails?.icon && <DynamicIcon icon={categoryDetails.icon as any} className="w-2 h-2" />}
               {category}
            </span>
          </div>
          <h4 className={`${CardStyles.title(isSelected)} text-center`}>
            {doc.filename}
          </h4>
          <div className="flex items-center justify-center space-x-2 mt-1">
            <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted">{new Date(doc.created_at).toLocaleDateString()}</p>
            <span className="text-[10px] text-gray-300 dark:text-dark-border">•</span>
            <span className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-tighter">{doc.filename.split('.').pop()}</span>
          </div>
        </div>
      </div>
    );
  }

  // List View
  return (
    <div 
      onClick={onClick}
      className={`${CardStyles.container(isSelected)} flex-shrink-0 ${className}`}
    >
      <div className={`${CardStyles.inner} flex items-center space-x-4`}>
        <div className={`w-12 h-12 rounded-xl overflow-hidden flex items-center justify-center flex-shrink-0 transition-colors
          ${doc.status === 'failed' ? 'bg-red-50 dark:bg-red-900/20 text-red-500' : 
            isSelected ? 'bg-blue-50 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400' : 'bg-gray-100 dark:bg-dark-bg text-gray-400 dark:text-dark-muted group-hover:bg-gray-200 dark:group-hover:bg-dark-border'}
        `}>
          {isImage ? (
            <AuthenticatedImage 
              documentId={doc.id} 
              className="w-full h-full object-cover opacity-90 group-hover:opacity-100 transition-opacity" 
            />
          ) : (
            getFileIcon(doc.filename)
          )}
        </div>

        <div className="overflow-hidden flex-1 min-w-0">
          <div className="flex items-center justify-between mb-1">
            <span 
              className="text-[9px] font-black uppercase tracking-[0.1em] truncate pr-2 flex items-center gap-1"
              style={{ color: categoryDetails?.color || '#9ca3af' }}
            >
               {categoryDetails?.icon && <DynamicIcon icon={categoryDetails.icon as any} className="w-2 h-2" />}
               {category}
            </span>
            <div className="flex items-center space-x-3">
              {doc.status === 'processing' && (
                <span className="flex h-1.5 w-1.5 rounded-full bg-blue-500 animate-pulse shadow-sm shadow-blue-500/50"></span>
              )}
              <span className={CardStyles.date(isSelected)}>
                {new Date(doc.created_at).toLocaleDateString()}
              </span>
            </div>
          </div>
          <h4 className={CardStyles.title(isSelected)}>
            {doc.filename}
          </h4>
          <div className={CardStyles.description}>
            <span className="uppercase tracking-tighter">{doc.filename.split('.').pop()}</span>
            {doc.size && <span className="ml-2">• {(doc.size / 1024).toFixed(1)} KB</span>}
          </div>
        </div>
      </div>
    </div>
  );
};
