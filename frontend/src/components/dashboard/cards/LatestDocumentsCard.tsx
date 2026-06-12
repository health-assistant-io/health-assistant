import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { 
  X, 
  FileText,
  Image as ImageIcon,
  Grid,
  List
} from 'lucide-react';
import { AuthenticatedThumbnail } from '../../ui/AuthenticatedThumbnail';

export const LatestDocumentsCard = React.forwardRef((props: any, ref: any) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { id, isEditMode, onRemove, style, className, onMouseDown, onMouseUp, onTouchEnd, data, config, onUpdateConfig } = props;
  const docs = data && data.length > 0 ? data : [];
  const viewMode = config?.viewMode || 'list';

  const toggleViewMode = (e: React.MouseEvent) => {
    e.stopPropagation();
    onUpdateConfig(id, { ...config, viewMode: viewMode === 'list' ? 'grid' : 'list' });
  };

  return (
    <div 
      ref={ref}
      style={style}
      className={`${className || ''} bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-6 flex flex-col relative group ${isEditMode ? '' : 'overflow-hidden'}`}
      onMouseDown={onMouseDown}
      onMouseUp={onMouseUp}
      onTouchEnd={onTouchEnd}
    >
      {isEditMode && onRemove && (
        <button 
          onClick={(e) => { e.stopPropagation(); onRemove(id); }}
          className="absolute -top-2 -right-2 bg-red-500 text-white rounded-full p-1 shadow-lg opacity-0 group-hover:opacity-100 transition-opacity z-[60] hover:bg-red-600 active:scale-95"
        >
          <X className="w-3 h-3" />
        </button>
      )}
      
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center space-x-2">
          <FileText className="w-5 h-5 text-indigo-500" />
          <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">{t('dashboard.cards.latest_documents')}</h3>
        </div>
        <div className="flex items-center space-x-2">
          <button 
            onClick={toggleViewMode}
            className="p-1.5 hover:bg-gray-100 dark:hover:bg-dark-border rounded-lg transition-colors text-gray-400 hover:text-blue-500"
            title={viewMode === 'list' ? 'Switch to Grid' : 'Switch to List'}
          >
            {viewMode === 'list' ? <Grid className="w-4 h-4" /> : <List className="w-4 h-4" />}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto no-scrollbar">
        {docs.length > 0 ? (
          viewMode === 'list' ? (
            <div className="space-y-3">
              {docs.map((doc: any) => (
                <div 
                  key={doc.id}
                  onClick={() => navigate(`/documents/${doc.id}`)}
                  className="flex items-center p-3 bg-gray-50/50 dark:bg-dark-bg/50 border border-gray-100 dark:border-dark-border rounded-xl hover:border-blue-200 dark:hover:border-blue-900 transition-all cursor-pointer group/item"
                >
                  <div className="w-10 h-10 rounded-lg bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border flex items-center justify-center mr-3 shadow-sm group-hover/item:border-blue-200">
                    {doc.filename.match(/\.(png|jpe?g|webp|gif|bmp)$/i) ? (
                      <ImageIcon className="w-5 h-5 text-blue-500" />
                    ) : (
                      <FileText className="w-5 h-5 text-gray-400" />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-bold text-gray-900 dark:text-dark-text truncate">{doc.filename}</p>
                    <p className="text-[10px] text-gray-400 uppercase font-bold tracking-tight">{doc.entities?.document_category || 'Document'}</p>
                  </div>
                  <div className="ml-3 text-right">
                    <p className="text-[10px] text-gray-400 font-bold">{new Date(doc.created_at).toLocaleDateString()}</p>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3">
              {docs.map((doc: any) => (
                <div 
                  key={doc.id}
                  onClick={() => navigate(`/documents/${doc.id}`)}
                  className="bg-gray-50/50 dark:bg-dark-bg/50 border border-gray-100 dark:border-dark-border rounded-xl overflow-hidden hover:border-blue-200 dark:hover:border-blue-900 transition-all cursor-pointer group/item p-2 flex flex-col h-full"
                >
                   <div className="aspect-video bg-white dark:bg-dark-surface rounded-lg mb-2 overflow-hidden flex items-center justify-center border border-gray-100 dark:border-dark-border">
                     {doc.filename.match(/\.(png|jpe?g|webp|gif|bmp)$/i) ? (
                       <AuthenticatedThumbnail documentId={doc.id} filename={doc.filename} className="object-cover w-full h-full" />
                     ) : (
                       <FileText className="w-8 h-8 text-gray-200" />
                     )}
                   </div>
                   <p className="text-[11px] font-bold text-gray-900 dark:text-dark-text truncate">{doc.filename}</p>
                   <p className="text-[9px] text-gray-400 font-bold uppercase truncate">{doc.entities?.document_category || 'Document'}</p>
                </div>
              ))}
            </div>
          )
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center opacity-40 py-8">
            <FileText className="w-12 h-12 text-gray-300 dark:text-dark-muted mb-2" />
            <p className="text-sm font-bold text-gray-500 dark:text-dark-muted">{t('dashboard.status.no_documents')}</p>
          </div>
        )}
      </div>
    </div>
  );
});
