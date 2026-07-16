import React, { useState, useEffect, useRef } from 'react';
import ReactCrop, { type Crop, type PixelCrop } from 'react-image-crop';
import 'react-image-crop/dist/ReactCrop.css';
import { AuthenticatedThumbnail } from './AuthenticatedThumbnail';
import { editDocument } from '../../services/documentService';
import { PerspectiveSelector } from './PerspectiveSelector';

interface ImageViewerProps {
  url: string;
  filename: string;
  onClose: () => void;
  category?: string;
  date?: string;
  relatedImages?: { id: string; title: string; type: string; category?: string }[];
  currentId?: string;
  parentId?: string;
  isEdited?: boolean;
  onSelectImage?: (id: string) => void;
  onRefresh?: () => void;
  /** Whether to allow the document-editing mode (crop / perspective /
   *  brightness-contrast "save"). Requires a persisted document + `currentId`
   *  to save against, so it's only meaningful for the document viewer. Off for
   *  ephemeral images (chat attachments, file previews) that have no backing
   *  document — the viewing tools (zoom/pan/brightness/contrast/invert/rotate/
   *  download) still work. Defaults to ``true`` for backward compatibility. */
  editable?: boolean;
}

export const ImageViewer: React.FC<ImageViewerProps> = ({
  url, filename, onClose, category, date, relatedImages, currentId,
  parentId, isEdited, onSelectImage, onRefresh, editable = true
}) => {
  const [scale, setScale] = useState(1);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  
  // Extra PACS-like features
  const [brightness, setBrightness] = useState(100);
  const [contrast, setContrast] = useState(100);
  const [invert, setInvert] = useState(false);
  const [rotation, setRotation] = useState(0);
  
  // Editing state
  const [isEditingMode, setIsEditingMode] = useState(false);
  const [editType, setEditType] = useState<'rect' | 'free'>('rect');
  
  const [crop, setCrop] = useState<Crop>({
    unit: '%',
    x: 10,
    y: 10,
    width: 80,
    height: 80
  });
  const [completedCrop, setCompletedCrop] = useState<PixelCrop | null>(null);
  
  const [perspectivePoints, setPerspectivePoints] = useState([
    { x: 10, y: 10 }, { x: 90, y: 10 }, { x: 90, y: 90 }, { x: 10, y: 90 }
  ]);
  const [isSaving, setIsSaving] = useState(false);
  
  const containerRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (isEditingMode) {
          setIsEditingMode(false);
        } else {
          onClose();
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose, isEditingMode]);

  const handleWheel = (e: React.WheelEvent) => {
    if (isEditingMode) return;
    e.preventDefault();
    const delta = e.deltaY < 0 ? 0.1 : -0.1;
    let newScale = scale + delta;
    if (newScale < 0.2) newScale = 0.2;
    if (newScale > 10) newScale = 10;
    setScale(newScale);
  };

  const handleMouseDown = (e: React.MouseEvent) => {
    if (isEditingMode) return;
    e.preventDefault();
    setIsDragging(true);
    setDragStart({ x: e.clientX - position.x, y: e.clientY - position.y });
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDragging || isEditingMode) return;
    setPosition({
      x: e.clientX - dragStart.x,
      y: e.clientY - dragStart.y
    });
  };

  const handleMouseUp = () => {
    setIsDragging(false);
  };

  const handleZoomIn = () => setScale(s => Math.min(s + 0.5, 10));
  const handleZoomOut = () => setScale(s => Math.max(s - 0.5, 0.2));
  
  const handleReset = () => {
    setScale(1);
    setPosition({ x: 0, y: 0 });
    setBrightness(100);
    setContrast(100);
    setInvert(false);
    setRotation(0);
    setCrop({
      unit: '%',
      x: 10,
      y: 10,
      width: 80,
      height: 80
    });
    setCompletedCrop(null);
    setPerspectivePoints([
      { x: 10, y: 10 }, { x: 90, y: 10 }, { x: 90, y: 90 }, { x: 10, y: 90 }
    ]);
  };

  const handleRotate = () => {
    setRotation(r => (r + 90) % 360);
    
    // Rotate perspective points
    setPerspectivePoints(prev => prev.map(p => ({
        x: 100 - p.y,
        y: p.x
    })));

    // Rotate crop
    setCrop(prev => ({
        ...prev,
        x: 100 - (prev.y + prev.height),
        y: prev.x,
        width: prev.height,
        height: prev.width
    }));
    
    setCompletedCrop(null); // Reset completed crop as dimensions changed
  };

  const increaseBrightness = () => setBrightness(b => Math.min(b + 20, 200));
  const decreaseBrightness = () => setBrightness(b => Math.max(b - 20, 20));
  
  const increaseContrast = () => setContrast(c => Math.min(c + 20, 200));
  const decreaseContrast = () => setContrast(c => Math.max(c - 20, 20));

  const handleSaveEdits = async () => {
    if (!currentId || !imgRef.current) return;
    setIsSaving(true);
    try {
      const image = imgRef.current;
      const params: any = {
        brightness: brightness / 100,
        contrast: contrast / 100,
        rotation: rotation,
      };

      // To avoid black screens, we must use naturalWidth/Height correctly.
      // The backend rotates FIRST, so coordinates must be relative to the rotated image size.
      const nw = image.naturalWidth;
      const nh = image.naturalHeight;
      const isRotatedPortrait = rotation === 90 || rotation === 270;
      const targetW = isRotatedPortrait ? nh : nw;
      const targetH = isRotatedPortrait ? nw : nh;

      if (editType === 'rect') {
        // Use the pixel crop if available, otherwise fallback to percentage calculation
        if (completedCrop) {
            // completedCrop values are in CSS pixels relative to the image's clientWidth/Height
            const scaleX = targetW / image.clientWidth;
            const scaleY = targetH / image.clientHeight;
            
            params.crop_left = Math.round(completedCrop.x * scaleX);
            params.crop_top = Math.round(completedCrop.y * scaleY);
            params.crop_right = Math.round((completedCrop.x + completedCrop.width) * scaleX);
            params.crop_bottom = Math.round((completedCrop.y + completedCrop.height) * scaleY);
        } else {
            // Percentage fallback
            const scaleX = targetW / 100;
            const scaleY = targetH / 100;
            params.crop_left = Math.round(crop.x * scaleX);
            params.crop_top = Math.round(crop.y * scaleY);
            params.crop_right = Math.round((crop.x + crop.width) * scaleX);
            params.crop_bottom = Math.round((crop.y + crop.height) * scaleY);
        }
      } else if (editType === 'free') {
        const scaleX = targetW / 100;
        const scaleY = targetH / 100;
        params.perspective_points = perspectivePoints.map(p => [
          Math.round(p.x * scaleX),
          Math.round(p.y * scaleY)
        ]);
      }

      const newDoc = await editDocument(currentId, params);
      setIsEditingMode(false);
      setRotation(0);
      
      if (onRefresh) {
        onRefresh();
      }
      
      if (onSelectImage) {
        onSelectImage(newDoc.id);
      }
    } catch (err) {
      console.error("Failed to save edits:", err);
      alert("Failed to save edits. Please try again.");
    } finally {
      setIsSaving(false);
    }
  };

  const handleDownloadFile = async () => {
    try {
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
      console.error("Failed to download image", err);
    }
  };

  return (
    <div className="fixed inset-0 z-[1000] flex items-center justify-center bg-black/95 backdrop-blur-sm">
      <div className="absolute top-0 left-0 right-0 p-4 flex flex-col sm:flex-row sm:items-center justify-between z-10 bg-gradient-to-b from-black/80 to-transparent gap-4">
        <div className="flex flex-col px-2">
          <div className="flex items-center gap-2">
            <span className="text-white font-bold truncate max-w-lg text-shadow-sm">{filename}</span>
            {category && (
              <span className="px-2 py-0.5 bg-blue-600 text-white text-[10px] font-black uppercase rounded tracking-wider shadow-sm">
                {category}
              </span>
            )}
            {isEdited && (
              <span className="px-2 py-0.5 bg-green-600 text-white text-[10px] font-black uppercase rounded tracking-wider shadow-sm">
                EDITED
              </span>
            )}
          </div>
          {date && (
            <span className="text-gray-400 text-xs font-medium mt-0.5 tracking-tight">
              Captured: {new Date(date).toLocaleDateString(undefined, { 
                weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' 
              })}
            </span>
          )}
        </div>
        
        <div className="flex flex-wrap items-center gap-4">
          <div className="flex items-center bg-white/10 rounded-lg p-1">
            {isEdited && parentId && (
              <>
                <button 
                  onClick={() => onSelectImage?.(parentId)}
                  className="px-3 py-2 text-white bg-blue-500/20 hover:bg-blue-500/40 rounded-md transition-colors text-xs font-bold uppercase tracking-wider"
                  title="View Original"
                >
                  View Original
                </button>
                <div className="w-px h-6 bg-white/20 mx-1"></div>
              </>
            )}
            <button 
              onClick={handleDownloadFile}
              className="p-2 text-white hover:bg-white/20 rounded-md transition-colors"
              title="Download Current Version"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" /></svg>
            </button>
            <div className="w-px h-6 bg-white/20 mx-1"></div>
            {editable && (
              <button
                onClick={() => setIsEditingMode(!isEditingMode)}
                className={`p-2 rounded-md transition-colors ${isEditingMode ? 'bg-blue-600 text-white' : 'text-white hover:bg-white/20'}`}
                title="Toggle Edit Mode"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" /></svg>
              </button>
            )}

            {isEditingMode && (
              <>
                <div className="w-px h-6 bg-white/20 mx-1"></div>
                <button 
                  onClick={() => setEditType('rect')} 
                  className={`px-3 py-2 rounded-md transition-colors text-xs font-bold uppercase ${editType === 'rect' ? 'bg-blue-500/40 text-white' : 'text-white/60 hover:text-white'}`}
                >
                  Rect
                </button>
                <button 
                  onClick={() => setEditType('free')} 
                  className={`px-3 py-2 rounded-md transition-colors text-xs font-bold uppercase ${editType === 'free' ? 'bg-blue-500/40 text-white' : 'text-white/60 hover:text-white'}`}
                >
                  Free
                </button>
              </>
            )}

            <div className="w-px h-6 bg-white/20 mx-1"></div>
            <button onClick={decreaseBrightness} className="p-2 text-white hover:bg-white/20 rounded-md transition-colors" title="Decrease Brightness">
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4" /></svg>
            </button>
            <span className="text-white text-xs font-medium px-2 select-none" title="Brightness">
              <svg className="w-4 h-4 inline-block" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" /></svg>
            </span>
            <button onClick={increaseBrightness} className="p-2 text-white hover:bg-white/20 rounded-md transition-colors" title="Increase Brightness">
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" /></svg>
            </button>
            
            <div className="w-px h-6 bg-white/20 mx-1"></div>
            <button onClick={decreaseContrast} className="p-2 text-white hover:bg-white/20 rounded-md transition-colors" title="Decrease Contrast">
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4" /></svg>
            </button>
            <span className="text-white text-xs font-medium px-2 select-none" title="Contrast">
              <svg className="w-4 h-4 inline-block" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" /></svg>
            </span>
            <button onClick={increaseContrast} className="p-2 text-white hover:bg-white/20 rounded-md transition-colors" title="Increase Contrast">
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" /></svg>
            </button>
            
            <div className="w-px h-6 bg-white/20 mx-1"></div>
            <button onClick={() => setInvert(!invert)} className={`p-2 hover:bg-white/20 rounded-md transition-colors ${invert ? 'text-blue-400 bg-white/10' : 'text-white'}`} title="Invert Colors">
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16V4m0 0L3 8m4-4l4 4m6 0v12m0 0l4-4m-4 4l-4-4" /></svg>
            </button>
            <div className="w-px h-6 bg-white/20 mx-1"></div>
            <button onClick={handleRotate} className="p-2 text-white hover:bg-white/20 rounded-md transition-colors" title="Rotate 90°">
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
            </button>
          </div>

          {!isEditingMode && (
            <div className="flex items-center bg-white/10 rounded-lg p-1">
              <button onClick={handleZoomOut} className="p-2 text-white hover:bg-white/20 rounded-md transition-colors" title="Zoom Out">
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM13 10H7" /></svg>
              </button>
              <span className="text-white text-sm font-medium w-12 text-center select-none">{Math.round(scale * 100)}%</span>
              <button onClick={handleZoomIn} className="p-2 text-white hover:bg-white/20 rounded-md transition-colors" title="Zoom In">
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v3m0 0v3m0-3h3m-3 0H7" /></svg>
              </button>
              <div className="w-px h-6 bg-white/20 mx-1"></div>
              <button onClick={handleReset} className="p-2 text-white hover:bg-white/20 rounded-md transition-colors" title="Reset All">
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
              </button>
            </div>
          )}

          {isEditingMode && (
            <button 
              onClick={handleSaveEdits}
              disabled={isSaving}
              className={`px-4 py-2 bg-green-600 hover:bg-green-500 text-white rounded-md font-bold text-sm flex items-center gap-2 shadow-lg transition-all ${isSaving ? 'opacity-50 cursor-not-allowed' : 'active:scale-95'}`}
            >
              {isSaving ? (
                <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"></div>
              ) : (
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
              )}
              {isSaving ? 'SAVING...' : 'SAVE CHANGES'}
            </button>
          )}
          
          <button onClick={onClose} className="p-2 text-white bg-white/10 hover:bg-red-500 rounded-full transition-colors ml-2" title="Close (Esc)">
            <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        </div>
      </div>

      <div 
        ref={containerRef}
        className={`w-full h-full overflow-hidden flex items-center justify-center ${isDragging ? 'cursor-grabbing' : 'cursor-grab'}`}
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      >
        {isEditingMode ? (
          <div 
            className="max-w-4xl max-h-[80vh] relative transition-transform duration-200"
            style={{ transform: `rotate(${rotation}deg)` }}
          >
            {editType === 'rect' ? (
              <ReactCrop 
                crop={crop} 
                onChange={c => setCrop(c)}
                onComplete={c => setCompletedCrop(c)}
              >
                <img 
                  ref={imgRef}
                  src={url} 
                  alt={filename}
                  style={{
                    filter: `brightness(${brightness}%) contrast(${contrast}%) ${invert ? 'invert(100%)' : ''}`,
                    willChange: 'filter'
                  }}
                  className="max-w-full max-h-full object-contain pointer-events-none select-none"
                />
              </ReactCrop>
            ) : (
              <div className="relative">
                <img 
                  ref={imgRef}
                  src={url} 
                  alt={filename}
                  style={{
                    filter: `brightness(${brightness}%) contrast(${contrast}%) ${invert ? 'invert(100%)' : ''}`,
                    willChange: 'filter'
                  }}
                  className="max-w-full max-h-full object-contain pointer-events-none select-none"
                />
                <PerspectiveSelector
                  points={perspectivePoints}
                  onChange={setPerspectivePoints}
                  imageWidth={imgRef.current?.width || 0}
                  imageHeight={imgRef.current?.height || 0}
                  rotation={rotation}
                />
              </div>
            )}
          </div>
        ) : (
          <img 
            src={url} 
            alt={filename}
            draggable={false}
            style={{
              transform: `translate(${position.x}px, ${position.y}px) scale(${scale}) rotate(${rotation}deg)`,
              filter: `brightness(${brightness}%) contrast(${contrast}%) ${invert ? 'invert(100%)' : ''}`,
              transition: isDragging ? 'none' : 'transform 0.1s ease-out',
              willChange: 'transform, filter'
            }}
            className="max-w-full max-h-full object-contain pointer-events-none select-none shadow-2xl"
          />
        )}
      </div>

      {!isEditingMode && relatedImages && relatedImages.length > 1 && (
        <div className="absolute bottom-0 left-0 right-0 p-6 flex justify-center z-10 bg-gradient-to-t from-black/80 to-transparent">
          <div className="flex items-center gap-3 px-4 py-3 bg-white/5 backdrop-blur-xl rounded-2xl border border-white/10 shadow-2xl overflow-x-auto max-w-[90vw] scrollbar-hide no-scrollbar">
            {relatedImages.map((img) => (
              <button
                key={img.id}
                onClick={() => onSelectImage?.(img.id)}
                className={`flex-shrink-0 w-16 h-16 rounded-xl overflow-hidden border-2 transition-all hover:scale-105 active:scale-95 ${
                  currentId === img.id ? 'border-blue-500 shadow-[0_0_15px_rgba(59,130,246,0.5)]' : 'border-transparent opacity-60 hover:opacity-100'
                }`}
                title={img.title}
              >
                <AuthenticatedThumbnail documentId={img.id} filename={img.title} className="w-full h-full object-cover" />
              </button>
            ))}
          </div>
        </div>
      )}
      
      <div className="absolute bottom-4 right-4 bg-black/60 text-white/70 text-xs px-3 py-2 rounded-lg pointer-events-none hidden sm:block">
        {isEditingMode ? 'Select area to crop • Adjust brightness/contrast above • Save changes' : 'Scroll to zoom • Click & drag to pan • Esc to close'}
      </div>
    </div>
  );
};
