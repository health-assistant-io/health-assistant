import React, { useState, useEffect, useRef } from 'react';
import { AuthenticatedThumbnail } from './AuthenticatedThumbnail';
import { getDicomMetadata, getDocumentPreviewUrl, getTempPreviewUrl } from '../../services/documentService';
import { useDicomFrames } from '../../hooks/useDicomFrames';
import { 
  X, ZoomIn, ZoomOut, RotateCw, Sun, Contrast, 
  Download, Maximize2, Minimize2, Settings, Info, 
  Activity, Shield, Calendar, User, Hospital, Focus, Map as MapIcon,
  Lock, AlertCircle, ChevronLeft, ChevronRight, LayoutGrid, List
} from 'lucide-react';

interface DicomViewerProps {
  url: string;
  filename: string;
  onClose: () => void;
  documentId?: string;
  category?: string;
  date?: string;
  relatedImages?: { id: string; title: string; type: string; category?: string }[];
  currentId?: string;
  onSelectImage?: (id: string) => void;
  onRefresh?: () => void;
  isLocal?: boolean;
  localFile?: File;
}

interface DicomFrameThumbnailProps {
  documentId?: string;
  localFile?: File;
  pageIndex: number;
  isSelected: boolean;
  onClick: () => void;
}

const DicomFrameThumbnail: React.FC<DicomFrameThumbnailProps> = ({ 
  documentId, localFile, pageIndex, isSelected, onClick 
}) => {
  const [url, setUrl] = useState<string>('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let isMounted = true;
    const fetchThumb = async () => {
      try {
        let resultUrl = '';
        if (localFile) {
          const res = await getTempPreviewUrl(localFile, pageIndex);
          resultUrl = res.url;
        } else if (documentId) {
          const previewObj = await getDocumentPreviewUrl(documentId, pageIndex);
          resultUrl = previewObj.url;
        }
        if (isMounted) {
          setUrl(resultUrl);
          setLoading(false);
        }
      } catch (err) {
        console.error("Failed to load frame thumbnail:", err);
        if (isMounted) setLoading(false);
      }
    };
    fetchThumb();
    return () => {
      isMounted = false;
    };
  }, [documentId, localFile, pageIndex]);

  return (
    <button 
      type="button"
      onClick={onClick}
      className={`aspect-square rounded-lg border-2 transition-all overflow-hidden relative group bg-gray-900 ${isSelected ? 'border-indigo-500 ring-2 ring-indigo-500/20 shadow-lg shadow-indigo-500/20' : 'border-white/5 opacity-50 hover:opacity-100 hover:border-white/20'}`}
    >
      {loading ? (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="w-4 h-4 border-t-2 border-indigo-500 rounded-full animate-spin"></div>
        </div>
      ) : url ? (
        <img src={url} className="w-full h-full object-cover" alt={`Frame ${pageIndex + 1}`} />
      ) : (
        <div className="absolute inset-0 flex items-center justify-center text-[10px] font-bold text-gray-600">
          #{pageIndex + 1}
        </div>
      )}
      <div className="absolute top-1 left-1 px-1 bg-black/60 rounded text-[8px] font-black text-white pointer-events-none">
        {pageIndex + 1}
      </div>
    </button>
  );
};

export const DicomViewer: React.FC<DicomViewerProps> = ({ 
  url, filename, onClose, documentId, category, date, relatedImages, currentId, 
  onSelectImage, onRefresh, isLocal = false, localFile
}) => {
  const [scale, setScale] = useState(1);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  
  // PACS-specific features
  const [brightness, setBrightness] = useState(100);
  const [contrast, setContrast] = useState(100);
  const [invert, setInvert] = useState(false);
  const [rotation, setRotation] = useState(0);
  
  // Gallery view state
  const [isGalleryOpen, setIsGalleryOpen] = useState(false);

  // Hook for Frame Management
  const { 
    currentPage, 
    totalPages, 
    currentUrl, 
    isLoading: isLoadingFrame, 
    error: loadError,
    loadFrame, 
    nextFrame, 
    prevFrame 
  } = useDicomFrames({ documentId: currentId, localFile });

  // Metadata state
  const [metadata, setMetadata] = useState<Record<string, { label: string; value: string }>>({});
  const [showMetadata, setShowMetadata] = useState(!isLocal);
  const [isLoadingMetadata, setIsLoadingMetadata] = useState(false);
  
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'ArrowLeft') prevFrame();
      if (e.key === 'ArrowRight') nextFrame();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose, prevFrame, nextFrame]);

  useEffect(() => {
    const fetchMetadata = async () => {
      if (!currentId || isLocal) return;
      setIsLoadingMetadata(true);
      try {
        const data = await getDicomMetadata(currentId);
        setMetadata(data);
      } catch (err) {
        console.error("Failed to load DICOM metadata:", err);
      } finally {
        setIsLoadingMetadata(false);
      }
    };
    fetchMetadata();
  }, [currentId, isLocal]);

  const handleWheel = (e: React.WheelEvent) => {
    if (e.altKey) {
        if (Math.abs(e.deltaY) > 50) {
            if (e.deltaY > 0) nextFrame();
            else prevFrame();
        }
    } else {
        e.preventDefault();
        const delta = e.deltaY < 0 ? 0.1 : -0.1;
        let newScale = scale + delta;
        if (newScale < 0.2) newScale = 0.2;
        if (newScale > 10) newScale = 10;
        setScale(newScale);
    }
  };

  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsDragging(true);
    setDragStart({ x: e.clientX - position.x, y: e.clientY - position.y });
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDragging) return;
    setPosition({
      x: e.clientX - dragStart.x,
      y: e.clientY - dragStart.y
    });
  };

  const handleMouseUp = () => {
    setIsDragging(false);
  };

  const handleReset = () => {
    setScale(1);
    setPosition({ x: 0, y: 0 });
    setBrightness(100);
    setContrast(100);
    setInvert(false);
    setRotation(0);
  };

  const handleDownloadFile = async () => {
    try {
      const response = await fetch(currentUrl);
      const blob = await response.blob();
      const blobUrl = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = blobUrl;
      link.download = `frame_${currentPage}_${filename}`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(blobUrl);
    } catch (err) {
      console.error("Failed to download image", err);
    }
  };

  return (
    <div className="fixed inset-0 z-[1000] flex flex-col bg-[#0a0a0a] text-gray-300 font-mono select-none">
      {/* Header Bar */}
      <div className="flex items-center justify-between px-6 py-4 bg-black/60 border-b border-white/5 backdrop-blur-md z-30">
        <div className="flex items-center gap-6">
          <div className="flex flex-col">
            <div className="flex items-center gap-2">
              <span className="text-white font-black text-sm uppercase tracking-widest">{filename}</span>
              <span className="px-2 py-0.5 bg-indigo-600 text-white text-[9px] font-black uppercase rounded tracking-tighter shadow-lg shadow-indigo-500/20">
                DICOM VIEW
              </span>
              {totalPages > 1 && (
                <span className="px-2 py-0.5 bg-white/10 text-gray-400 text-[9px] font-black uppercase rounded border border-white/5">
                  STACK: {totalPages} FRAMES
                </span>
              )}
            </div>
            <div className="flex items-center gap-4 mt-1">
               <span className="text-[10px] text-gray-500 font-bold uppercase flex items-center gap-1.5">
                  <Activity className="w-3 h-3" />
                  ID: {isLocal ? 'LOCAL_STAGING' : (currentId?.substring(0, 8) || 'UNKNOWN')}
               </span>
               <span className="text-[10px] text-gray-500 font-bold uppercase flex items-center gap-1.5">
                  <Calendar className="w-3 h-3" />
                  FRAME: {currentPage + 1} / {totalPages}
               </span>
            </div>
          </div>
        </div>
        
        <div className="flex items-center gap-3">
          <div className="flex items-center bg-white/5 rounded-xl p-1.5 border border-white/5 shadow-inner">
            <button 
              type="button"
              onClick={() => setIsGalleryOpen(!isGalleryOpen)} 
              className={`p-2 rounded-lg transition-all ${isGalleryOpen ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:bg-white/10'}`}
              title="Toggle Frame Gallery"
            >
              <LayoutGrid className="w-4 h-4" />
            </button>
            <div className="w-px h-6 bg-white/10 mx-1"></div>
            <button 
              type="button"
              onClick={() => setInvert(!invert)} className={`p-2 hover:bg-white/10 rounded-lg transition-all ${invert ? 'text-indigo-400' : 'text-gray-400'}`} title="Invert Colors">
              <Maximize2 className="w-4 h-4" />
            </button>
            <button 
              type="button"
              onClick={() => setRotation(r => (r + 90) % 360)} className="p-2 text-gray-400 hover:bg-white/10 rounded-lg transition-all" title="Rotate 90°">
              <RotateCw className="w-4 h-4" />
            </button>
            <div className="w-px h-6 bg-white/10 mx-1"></div>
            <button 
              type="button"
              onClick={() => setBrightness(b => Math.max(b - 10, 20))} className="p-2 text-gray-400 hover:bg-white/10 rounded-lg transition-all">
              <Sun className="w-4 h-4 opacity-50" />
            </button>
            <button 
              type="button"
              onClick={() => setBrightness(b => Math.min(b + 10, 200))} className="p-2 text-gray-400 hover:bg-white/10 rounded-lg transition-all">
              <Sun className="w-4 h-4" />
            </button>
            <div className="w-px h-6 bg-white/10 mx-1"></div>
            <button 
              type="button"
              onClick={() => setContrast(c => Math.max(c - 10, 20))} className="p-2 text-gray-400 hover:bg-white/10 rounded-lg transition-all">
              <Contrast className="w-4 h-4 opacity-50" />
            </button>
            <button 
              type="button"
              onClick={() => setContrast(c => Math.min(c + 10, 200))} className="p-2 text-gray-400 hover:bg-white/10 rounded-lg transition-all">
              <Contrast className="w-4 h-4" />
            </button>
            <div className="w-px h-6 bg-white/10 mx-1"></div>
            <button 
              type="button"
              onClick={() => setScale(s => Math.max(s - 0.2, 0.2))} className="p-2 text-gray-400 hover:bg-white/10 rounded-lg transition-all">
              <ZoomOut className="w-4 h-4" />
            </button>
            <span className="text-[10px] font-black text-gray-400 w-12 text-center uppercase tracking-tighter">
              {Math.round(scale * 100)}%
            </span>
            <button 
              type="button"
              onClick={() => setScale(s => Math.min(s + 0.2, 10))} className="p-2 text-gray-400 hover:bg-white/10 rounded-lg transition-all">
              <ZoomIn className="w-4 h-4" />
            </button>
            <div className="w-px h-6 bg-white/10 mx-1"></div>
            <button 
              type="button"
              onClick={handleReset} className="p-2 text-gray-400 hover:bg-white/10 rounded-lg transition-all" title="Reset Viewer">
              <Settings className="w-4 h-4" />
            </button>
          </div>
          
          {!isLocal && (
            <button 
              type="button"
              onClick={() => setShowMetadata(!showMetadata)}
              className={`p-2.5 rounded-xl transition-all shadow-lg ${showMetadata ? 'bg-indigo-600 text-white' : 'bg-white/5 text-gray-400 hover:bg-white/10'}`}
              title="DICOM Metadata"
            >
              <Info className="w-5 h-5" />
            </button>
          )}
          
          <button 
            type="button"
            onClick={handleDownloadFile}
            className="p-2.5 bg-white/5 text-gray-400 hover:bg-white/10 rounded-xl transition-all shadow-lg"
            title="Download Current Frame"
          >
            <Download className="w-5 h-5" />
          </button>
          
          <button 
            type="button"
            onClick={onClose} className="p-2.5 bg-red-600/10 text-red-500 hover:bg-red-600 hover:text-white rounded-xl transition-all shadow-lg shadow-red-500/10">
            <X className="w-6 h-6" />
          </button>
        </div>
      </div>

      <div className="flex-1 flex overflow-hidden relative">
        {/* Frame Gallery Sidebar */}
        {isGalleryOpen && totalPages > 1 && (
          <div className="w-64 bg-black/80 border-r border-white/10 overflow-y-auto p-4 flex flex-col gap-4 z-20 animate-in slide-in-from-left duration-300">
             <p className="text-[10px] font-black text-gray-500 uppercase tracking-widest mb-2 flex items-center justify-between">
                <span>Stack Frames</span>
                <span className="bg-white/5 px-2 py-0.5 rounded text-gray-400">{totalPages}</span>
             </p>
             <div className="grid grid-cols-2 gap-3">
                {Array.from({ length: totalPages }).map((_, i) => (
                  <DicomFrameThumbnail
                    key={i}
                    documentId={currentId}
                    localFile={localFile}
                    pageIndex={i}
                    isSelected={currentPage === i}
                    onClick={() => loadFrame(i)}
                  />
                ))}
             </div>
          </div>
        )}

        {/* Metadata Sidebar */}
        {showMetadata && !isLocal && (
          <div className="absolute right-6 top-6 bottom-6 w-80 bg-black/60 backdrop-blur-xl rounded-2xl border border-white/5 overflow-y-auto z-20 shadow-2xl animate-in slide-in-from-right-4 duration-500">
            <div className="p-6">
              <div className="flex items-center justify-between mb-6">
                <h3 className="text-xs font-black text-white uppercase tracking-widest flex items-center gap-2">
                  <Shield className="w-3 h-3 text-indigo-400" />
                  Clinical Metadata
                </h3>
                <button 
                  type="button"
                  onClick={() => setShowMetadata(false)} className="text-gray-500 hover:text-white">
                  <X className="w-4 h-4" />
                </button>
              </div>
              
              {isLoadingMetadata ? (
                <div className="space-y-4">
                  {[1, 2, 3, 4, 5, 6].map(i => (
                    <div key={i} className="h-10 bg-white/5 rounded-lg animate-pulse" />
                  ))}
                </div>
              ) : (
                <div className="space-y-4">
                    <div className="bg-white/5 rounded-xl p-3 border border-white/5">
                      <p className="text-[9px] font-black text-indigo-400 uppercase tracking-widest mb-3 flex items-center gap-2">
                        <User className="w-3 h-3" />
                        Patient Identification
                      </p>
                      <div className="space-y-2.5">
                        {['PatientName', 'PatientID', 'PatientBirthDate', 'PatientSex'].map(tag => metadata[tag] && (
                          <div key={tag} className="flex flex-col">
                            <span className="text-[9px] text-gray-500 font-bold uppercase tracking-tighter">{metadata[tag].label}</span>
                            <span className="text-[11px] text-white font-medium">{metadata[tag].value}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Main Viewer Area */}
        <div 
          ref={containerRef}
          className={`flex-1 overflow-hidden flex items-center justify-center bg-black/40 ${isDragging ? 'cursor-grabbing' : 'cursor-grab'} relative`}
          onWheel={handleWheel}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
        >
          {isLoadingFrame && (
             <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/20 backdrop-blur-[2px]">
                <div className="flex flex-col items-center gap-4">
                   <div className="w-12 h-12 border-t-2 border-indigo-500 rounded-full animate-spin"></div>
                   <span className="text-[10px] font-black text-indigo-400 uppercase tracking-widest">Decoding Frame {currentPage + 1}</span>
                </div>
             </div>
          )}

          {loadError || !currentUrl ? (
            <div className="flex flex-col items-center gap-8 animate-in fade-in duration-700">
               <div className="p-10 bg-red-600/10 rounded-[3rem] border border-red-500/20">
                 <AlertCircle className="w-20 h-20 text-red-500" />
               </div>
               <p className="text-white font-black text-xl uppercase tracking-[0.3em]">{loadError || 'Decoding Failure'}</p>
            </div>
          ) : (
            <img 
              src={currentUrl} 
              alt={filename}
              draggable={false}
              style={{
                transform: `translate(${position.x}px, ${position.y}px) scale(${scale}) rotate(${rotation}deg)`,
                filter: `brightness(${brightness}%) contrast(${contrast}%) ${invert ? 'invert(100%)' : ''}`,
                transition: isDragging ? 'none' : 'transform 0.15s ease-out',
                willChange: 'transform, filter'
              }}
              className="max-w-[90%] max-h-[90%] object-contain pointer-events-none select-none shadow-[0_0_100px_rgba(0,0,0,0.8)] border border-white/5"
            />
          )}

          {/* Navigation Overlay */}
          {totalPages > 1 && (
            <div className="absolute bottom-10 left-1/2 -translate-x-1/2 flex items-center gap-6 bg-black/60 backdrop-blur-xl px-8 py-4 rounded-2xl border border-white/10 z-20 shadow-2xl">
                <button 
                  type="button"
                  onClick={prevFrame} disabled={currentPage === 0} className="p-2 text-gray-400 hover:text-white disabled:opacity-20 transition-all">
                  <ChevronLeft className="w-6 h-6" />
                </button>
                <div className="flex flex-col items-center min-w-[120px]">
                  <span className="text-[10px] font-black text-indigo-400 uppercase tracking-[0.2em]">Frame Progress</span>
                  <span className="text-lg font-black text-white">{currentPage + 1} / {totalPages}</span>
                  <div className="w-full h-1 bg-white/5 rounded-full mt-2 overflow-hidden">
                     <div className="h-full bg-indigo-500 transition-all duration-300" style={{ width: `${((currentPage + 1) / totalPages) * 100}%` }}></div>
                  </div>
                </div>
                <button 
                  type="button"
                  onClick={nextFrame} disabled={currentPage === totalPages - 1} className="p-2 text-gray-400 hover:text-white disabled:opacity-20 transition-all">
                  <ChevronRight className="w-6 h-6" />
                </button>
            </div>
          )}

          {/* DICOM HUD */}
          <div className="absolute inset-0 pointer-events-none p-10 select-none z-10">
             <div className="absolute top-10 left-10 flex flex-col gap-1">
                <p className="text-white text-xs font-black uppercase tracking-widest drop-shadow-lg">
                  {metadata['PatientName']?.value || (isLocal ? 'LOCAL STAGING' : 'ANONYMIZED PATIENT')}
                </p>
                <p className="text-gray-400 text-[10px] font-bold drop-shadow-lg uppercase">
                  ID: {metadata['PatientID']?.value || (isLocal ? 'PENDING' : 'N/A')}
                </p>
             </div>
             <div className="absolute bottom-10 right-10 text-right">
                <p className="text-gray-400 text-[10px] font-bold drop-shadow-lg uppercase">
                  ZOOM: {Math.round(scale * 100)}% • FRAME: {currentPage + 1}
                </p>
                <div className="flex items-center justify-end gap-2 mt-2">
                  <div className="w-12 h-0.5 bg-white/20"></div>
                  <span className="text-white text-[10px] font-black">10cm</span>
                </div>
             </div>
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="px-6 py-3 flex items-center justify-between bg-black text-[9px] font-black text-gray-600 uppercase tracking-[0.3em] z-30 border-t border-white/5">
         <div className="flex items-center gap-6">
            <span className="flex items-center gap-2">
              <Shield className="w-3 h-3 text-gray-700" />
              PACS v1.4 • High Fidelity Protocol
            </span>
         </div>
         <p>Health Assistant Engine • Frame Mapping Enabled</p>
      </div>
    </div>
  );
};
