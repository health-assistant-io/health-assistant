import React, { useCallback, useEffect, useRef, useState } from 'react';
import ReactCrop, { type Crop, type PixelCrop } from 'react-image-crop';
import 'react-image-crop/dist/ReactCrop.css';

interface Props {
  /** The source image to crop, as an object URL or data URL (set by the parent
   *  from an uploaded file). Empty string = no source yet. */
  sourceUrl: string;
  /** Called with the cropped WebP Blob whenever the crop selection changes.
   *  Null when there is no valid selection. */
  onCropChange: (blob: Blob | null) => void;
  className?: string;
}

const MIN_DIM = 40;

/**
 * Interactive image cropper for body figures. Renders the source image inside
 * react-image-crop; as the selection is dragged/resized, a canvas performs a
 * REAL pixel crop and produces a WebP blob. The parent uploads this blob as
 * the figure's image — no viewBox or coordinate-space tricks, the stored image
 * IS the view.
 */
export const ImageCropEditor: React.FC<Props> = ({ sourceUrl, onCropChange, className = '' }) => {
  const imgRef = useRef<HTMLImageElement>(null);
  const [crop, setCrop] = useState<Crop>({ unit: '%', x: 0, y: 0, width: 100, height: 100 });

  // Default the selection to the full image when a new source loads.
  useEffect(() => {
    setCrop({ unit: '%', x: 0, y: 0, width: 100, height: 100 });
    onCropChange(null);
  }, [sourceUrl]); // eslint-disable-line react-hooks/exhaustive-deps

  const produceBlob = useCallback(async (px: PixelCrop) => {
    if (!imgRef.current) { onCropChange(null); return; }
    const img = imgRef.current;
    const scaleX = img.naturalWidth / img.width;
    const scaleY = img.naturalHeight / img.height;
    const sx = Math.round(px.x * scaleX);
    const sy = Math.round(px.y * scaleY);
    const sw = Math.round(px.width * scaleX);
    const sh = Math.round(px.height * scaleY);
    if (sw < MIN_DIM || sh < MIN_DIM) { onCropChange(null); return; }
    const canvas = document.createElement('canvas');
    canvas.width = sw;
    canvas.height = sh;
    const ctx = canvas.getContext('2d');
    if (!ctx) { onCropChange(null); return; }
    ctx.drawImage(img, sx, sy, sw, sh, 0, 0, sw, sh);
    canvas.toBlob((blob) => onCropChange(blob), 'image/webp', 0.92);
  }, [onCropChange]);

  const onComplete = (c: PixelCrop) => produceBlob(c);

  // When a source image finishes loading, emit a blob for the default
  // full-image selection. Without this, a user who uploads an image but never
  // drags the crop selection leaves onCropChange(null), and the parent rejects
  // the save (the whole point of "no crop = save everything").
  const onImageLoad = useCallback(() => {
    const img = imgRef.current;
    if (!img) return;
    produceBlob({ unit: 'px', x: 0, y: 0, width: img.width, height: img.height });
  }, [produceBlob]);

  if (!sourceUrl) {
    return (
      <div className={`flex items-center justify-center text-xs text-gray-400 bg-gray-50 dark:bg-dark-bg rounded-xl py-12 ${className}`}>
        Upload a source image first to crop it.
      </div>
    );
  }

  return (
    <div className={`bg-gray-50 dark:bg-dark-bg rounded-xl p-2 overflow-hidden ${className}`}>
      <ReactCrop
        crop={crop}
        onChange={setCrop}
        onComplete={onComplete}
        minWidth={10}
        minHeight={10}
        keepSelection
        aspect={undefined}
      >
        <img
          ref={imgRef}
          src={sourceUrl}
          alt="source"
          className="max-w-full max-h-[320px] object-contain"
          crossOrigin="anonymous"
          onLoad={onImageLoad}
        />
      </ReactCrop>
    </div>
  );
};
