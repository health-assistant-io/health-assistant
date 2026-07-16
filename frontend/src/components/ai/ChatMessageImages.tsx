import React, { useState } from 'react';
import { ZoomIn } from 'lucide-react';
import { ImageViewer } from '../ui/ImageViewer';

interface Props {
  /** RFC 2397 data URLs of the images attached to the message. */
  images: string[];
  /** When true (user-sent bubbles) the gallery renders on a light tint. */
  variant?: 'user' | 'assistant';
}

/**
 * Renders the image attachments of a chat message inside its bubble.
 *
 * Layout adapts to the image count so galleries always look intentional:
 *   * 1 image — single large tile (capped height).
 *   * 2 images — two side-by-side tiles.
 *   * 3 images — one wide tile + a 2-tile row.
 *   * 4+ images — a 2x2 grid (extras folded behind a "+N" overlay).
 *
 * Each tile opens a full-screen lightbox on click. Images are rendered from
 * data URLs (already on the client) so no auth or network is needed.
 */
export const ChatMessageImages: React.FC<Props> = ({ images, variant = 'user' }) => {
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);
  if (!images || images.length === 0) return null;

  const count = images.length;
  const visible = images.slice(0, 4);
  const overflow = count - visible.length;

  const tileTint =
    variant === 'user'
      ? 'bg-white/15 ring-white/20'
      : 'bg-gray-100 dark:bg-dark-bg ring-gray-200 dark:ring-dark-border';

  const renderTile = (src: string, index: number, span?: boolean) => (
    <button
      type="button"
      key={`${src.slice(-16)}-${index}`}
      onClick={() => setLightboxIndex(index)}
      className={`group/tile relative block overflow-hidden rounded-xl ring-1 ${tileTint} ${
        span ? 'col-span-2 aspect-[16/9]' : 'aspect-square'
      }`}
    >
      <img
        src={src}
        alt={`Attachment ${index + 1}`}
        loading="lazy"
        className="w-full h-full object-cover transition-transform duration-300 group-hover/tile:scale-105"
      />
      <span className="absolute inset-0 bg-black/0 group-hover/tile:bg-black/20 transition-colors flex items-center justify-center">
        <ZoomIn className="w-4 h-4 text-white opacity-0 group-hover/tile:opacity-100 transition-opacity" />
      </span>
      {index === visible.length - 1 && overflow > 0 && (
        <span className="absolute inset-0 bg-black/60 flex items-center justify-center text-white text-lg font-black">
          +{overflow}
        </span>
      )}
    </button>
  );

  return (
    <>
      <div className="not-prose mt-2">
        {count === 1 && (
          <button
            type="button"
            onClick={() => setLightboxIndex(0)}
            className={`group/single block w-full max-w-xs overflow-hidden rounded-xl ring-1 ${tileTint}`}
          >
            <img
              src={images[0]}
              alt="Attachment 1"
              className="w-full max-h-64 object-cover transition-transform duration-300 group-hover/single:scale-105"
            />
          </button>
        )}
        {count === 2 && (
          <div className="grid grid-cols-2 gap-1.5 max-w-xs">{images.map((src, i) => renderTile(src, i))}</div>
        )}
        {count === 3 && (
          <div className="grid grid-cols-2 gap-1.5 max-w-sm">
            {renderTile(images[0], 0, true)}
            {images.slice(1, 3).map((src, i) => renderTile(src, i + 1))}
          </div>
        )}
        {count >= 4 && (
          <div className="grid grid-cols-2 gap-1.5 max-w-sm">
            {visible.map((src, i) => renderTile(src, i))}
          </div>
        )}
      </div>

      {lightboxIndex !== null && (
        <>
          <ImageViewer
            key={lightboxIndex}
            url={images[lightboxIndex]}
            filename={`Attachment ${lightboxIndex + 1}`}
            editable={false}
            onClose={() => setLightboxIndex(null)}
          />
          {images.length > 1 && (
            <>
              <NavArrow
                side="left"
                onClick={() =>
                  setLightboxIndex((i) =>
                    i === null ? i : (i - 1 + images.length) % images.length,
                  )
                }
              />
              <NavArrow
                side="right"
                onClick={() =>
                  setLightboxIndex((i) =>
                    i === null ? i : (i + 1) % images.length,
                  )
                }
              />
            </>
          )}
        </>
      )}
    </>
  );
};

/** Floating prev/next arrow rendered above the ImageViewer (z-[1001]) for
 *  navigating a multi-image chat message. The ImageViewer itself only has a
 *  related-images strip for persisted documents, so chat data URLs need their
 *  own lightweight nav. */
const NavArrow: React.FC<{ side: 'left' | 'right'; onClick: () => void }> = ({
  side,
  onClick,
}) => (
  <button
    type="button"
    onClick={onClick}
    className={`fixed top-1/2 -translate-y-1/2 z-[1001] p-3 rounded-full bg-white/10 hover:bg-white/25 text-white backdrop-blur-sm transition-all active:scale-90 ${
      side === 'left' ? 'left-4' : 'right-4'
    }`}
    title={side === 'left' ? 'Previous image' : 'Next image'}
  >
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d={side === 'left' ? 'M15 19l-7-7 7-7' : 'M9 5l7 7-7 7'}
      />
    </svg>
  </button>
);
