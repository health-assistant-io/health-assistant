import React, { useEffect } from 'react';
import { useAnatomyAtlas } from './atlas';
import type { AnatomyFigure } from '../../types/anatomy';

interface Props {
  /** Which figure view to render (slug, e.g. "man-front"). */
  figureSlug: string;
  className?: string;
  children?: React.ReactNode;
  dim?: boolean;
  overlayInteractive?: boolean;
}

/**
 * Renders a body figure view from the DB-driven atlas as an `<img>` (fetched
 * as an authenticated blob). Children (markers, hotspots) render in an
 * absolutely-positioned SVG overlay whose viewBox matches the image's pixel
 * dimensions, so normalized marker coords (0-1) resolve to the correct spot
 * at any render size.
 */
export const BodySilhouette: React.FC<Props> = ({
  figureSlug,
  className = '',
  children,
  dim = false,
  overlayInteractive = false,
}) => {
  const figure = useAnatomyAtlas((s) => s.figures[figureSlug]);
  const imageUrl = useAnatomyAtlas((s) => s.imageUrls[figureSlug]);
  const getImage = useAnatomyAtlas((s) => s.getImage);
  const ensureLoaded = useAnatomyAtlas((s) => s.ensureLoaded);

  useEffect(() => {
    ensureLoaded();
  }, [ensureLoaded]);

  useEffect(() => {
    if (figureSlug && figure && !imageUrl) getImage(figureSlug);
  }, [figureSlug, figure, imageUrl, getImage]);

  const w = figure?.width || 100;
  const h = figure?.height || 200;

  return (
    <div className={`relative ${className}`}>
      <img
        src={imageUrl ?? ''}
        alt={figure?.label ?? figureSlug}
        className="w-full h-auto block"
        style={{ filter: dim ? 'opacity(0.45) saturate(0.85)' : undefined }}
        draggable={false}
      />
      <svg
        viewBox={`0 0 ${w} ${h}`}
        preserveAspectRatio="xMidYMid meet"
        className={`absolute inset-0 w-full h-full ${overlayInteractive ? '' : 'pointer-events-none'}`}
      >
        {children}
      </svg>
    </div>
  );
};

/** Resolve a marker to absolute pixel coordinates for the given figure. */
export function markerToCanvas(fig: AnatomyFigure | null | undefined, m: { nx: number; ny: number; nr?: number }) {
  const w = fig?.width || 100;
  const h = fig?.height || 200;
  return {
    cx: m.nx * w,
    cy: m.ny * h,
    r: (m.nr || 0.015) * h,
  };
}

/** Resolve a pointer position (client coords) to normalized figure coords. */
export function pointerToFigureNormalized(
  fig: AnatomyFigure | null | undefined,
  rect: DOMRect,
  clientX: number,
  clientY: number,
): { nx: number; ny: number } | null {
  if (!fig || !fig.width || !fig.height) return null;
  const containerAR = rect.width / rect.height;
  const imgAR = fig.width / fig.height;
  let drawW = rect.width;
  let drawH = rect.height;
  let offX = 0;
  let offY = 0;
  if (containerAR > imgAR) {
    drawW = rect.height * imgAR;
    offX = (rect.width - drawW) / 2;
  } else {
    drawH = rect.width / imgAR;
    offY = (rect.height - drawH) / 2;
  }
  const nx = (clientX - rect.left - offX) / drawW;
  const ny = (clientY - rect.top - offY) / drawH;
  return {
    nx: Math.max(0, Math.min(1, nx)),
    ny: Math.max(0, Math.min(1, ny)),
  };
}
