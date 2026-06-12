import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { 
  X, 
  Image as ImageIcon,
  ChevronLeft,
  ChevronRight,
  Maximize2,
  FileText,
  Download,
  ExternalLink
} from 'lucide-react';
import { AuthenticatedThumbnail } from '../../ui/AuthenticatedThumbnail';
import { AuthenticatedImageViewer } from '../../ui/AuthenticatedImageViewer';
import { getDocumentDownloadUrl } from '../../../services/documentService';

export const ImageViewerCard = React.forwardRef((props: any, ref: any) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { id, isEditMode, onRemove, style, className, onMouseDown, onMouseUp, onTouchEnd, children, data
  } = props;
  const studies = data && data.length > 0 ? data : [];
  const [currentIndex, setCurrentIndex] = React.useState(0);
  const [isFullScreen, setIsFullScreen] = React.useState(false);

  const nextSlide = (e?: React.MouseEvent) => {
    e?.stopPropagation();
    setCurrentIndex((prev) => (prev + 1) % studies.length);
  };

  const prevSlide = (e?: React.MouseEvent) => {
    e?.stopPropagation();
    setCurrentIndex((prev) => (prev - 1 + studies.length) % studies.length);
  };

  const currentStudy = studies[currentIndex];

  const handleDownload = async (e: React.MouseEvent, id: string, filename: string) => {
    e.stopPropagation();
    try {
      const url = await getDocumentDownloadUrl(id);
      const response = await fetch(url);
      const blob = await response.blob();
      const blobUrl = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = blobUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(blobUrl);
    } catch (err) {
      console.error("Failed to download file", err);
    }
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
          <ImageIcon className="w-5 h-5 text-blue-500" />
          <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">{t('dashboard.cards.image_viewer')}</h3>
        </div>
        {studies.length > 1 && (
          <div className="flex items-center space-x-1">
            <button 
              onClick={prevSlide}
              className="p-1 hover:bg-gray-100 dark:hover:bg-dark-border rounded-full transition-colors"
            >
              <ChevronLeft className="w-4 h-4 text-gray-400" />
            </button>
            <span className="text-[10px] font-bold text-gray-400 dark:text-dark-muted">
              {currentIndex + 1} / {studies.length}
            </span>
            <button 
              onClick={nextSlide}
              className="p-1 hover:bg-gray-100 dark:hover:bg-dark-border rounded-full transition-colors"
            >
              <ChevronRight className="w-4 h-4 text-gray-400" />
            </button>
          </div>
        )}
      </div>

      <div className="flex-1 min-h-0 flex flex-col">
        {studies.length > 0 ? (
          <div className="flex-1 flex flex-col space-y-4 min-h-0">
            <div 
              className="relative flex-1 bg-gray-950 rounded-2xl overflow-hidden group/img flex items-center justify-center border border-gray-800 dark:border-dark-border min-h-0 cursor-pointer"
              onClick={() => setIsFullScreen(true)}
            >
              {currentStudy.type === 'document' ? (
                <AuthenticatedThumbnail 
                  documentId={currentStudy.id} 
                  filename={currentStudy.title} 
                  className="object-contain transition-all duration-500 group-hover/img:scale-[1.02]"
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center bg-gray-900 dark:bg-dark-bg">
                  <ImageIcon className="w-12 h-12 text-gray-700 dark:text-dark-muted" />
                </div>
              )}
              
              <div className="absolute top-3 left-3">
                <span className="px-2 py-1 bg-white/90 dark:bg-dark-surface/90 backdrop-blur-sm border border-gray-100 dark:border-dark-border rounded-lg text-[10px] font-bold text-blue-600 dark:text-blue-400 shadow-sm uppercase tracking-wider">
                  {currentStudy.category}
                </span>
              </div>

              <div className="absolute inset-0 bg-black/20 opacity-0 group-hover/img:opacity-100 transition-opacity flex items-center justify-center">
                <div className="bg-white/10 backdrop-blur-md p-3 rounded-full border border-white/20">
                  <Maximize2 className="w-6 h-6 text-white" />
                </div>
              </div>
            </div>

            <div className="flex items-end justify-between gap-4">
              <div className="min-w-0 flex-1">
                <h4 className="font-bold text-gray-900 dark:text-dark-text truncate text-sm leading-tight mb-1">
                  {currentStudy.title}
                </h4>
                <p className="text-xs text-gray-400 dark:text-dark-muted font-medium">
                  {new Date(currentStudy.date).toLocaleDateString(undefined, { 
                    year: 'numeric', 
                    month: 'short', 
                    day: 'numeric' 
                  })}
                </p>
              </div>

              <div className="flex flex-shrink-0 space-x-2">
                {currentStudy.examination_id && (
                  <button 
                    onClick={() => navigate(`/examinations/${currentStudy.examination_id}`)}
                    className="flex items-center space-x-1.5 px-3 py-1.5 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-xl text-[11px] font-bold hover:bg-blue-100 dark:hover:bg-blue-900/40 transition-colors border border-blue-100 dark:border-blue-900/30 shadow-sm shadow-blue-50 dark:shadow-none active:scale-95"
                    title={t('common.open_exam')}
                  >
                    <FileText className="w-3 h-3" />
                    <span>{t('common.open_exam')}</span>
                  </button>
                )}
                {currentStudy.type === 'document' && (
                  <>
                    <button 
                      onClick={(e) => handleDownload(e, currentStudy.id, currentStudy.title)}
                      className="p-1.5 bg-gray-50 dark:bg-dark-bg text-gray-400 dark:text-dark-muted rounded-xl hover:bg-gray-100 dark:hover:bg-dark-border hover:text-gray-600 dark:hover:text-dark-text transition-colors border border-transparent"
                      title={t('common.download')}
                    >
                      <Download className="w-3.5 h-3.5" />
                    </button>
                    <button 
                      onClick={() => navigate(`/documents/${currentStudy.id}`)}
                      className="p-1.5 bg-gray-50 dark:bg-dark-bg text-gray-400 dark:text-dark-muted rounded-xl hover:bg-gray-100 dark:hover:bg-dark-border hover:text-gray-600 dark:hover:text-dark-text transition-colors border border-transparent"
                      title={t('common.view_original')}
                    >
                      <ExternalLink className="w-3.5 h-3.5" />
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center opacity-40 bg-gray-50 dark:bg-dark-bg rounded-2xl border border-dashed border-gray-200 dark:border-dark-border py-8">
            <ImageIcon className="w-12 h-12 text-gray-300 dark:text-dark-muted mb-2" />
            <p className="text-sm font-bold text-gray-500 dark:text-dark-muted">{t('dashboard.status.no_imaging')}</p>
          </div>
        )}
      </div>

      {/* Full Screen Modal */}
      {isFullScreen && currentStudy && currentStudy.type === 'document' && (
        <AuthenticatedImageViewer 
          documentId={currentStudy.id}
          filename={currentStudy.title}
          category={currentStudy.category}
          date={currentStudy.date}
          gallery={studies}
          onClose={() => setIsFullScreen(false)}
        />
      )}

      {children}
    </div>
  );
});
