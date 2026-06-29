import React from 'react';
import { BodySilhouette, markerToCanvas } from './BodySilhouette';
import { useAnatomyAtlas } from './atlas';
import type { AnatomyMapMarker } from '../../types/anatomy';

interface Props {
  /** Marker (normalized 0–1 within the figure's viewBox), or null to hide. */
  marker?: AnatomyMapMarker | null;
  /** Which body figure to render. */
  figureSlug: string;
  /** Label shown beneath the silhouette. */
  label?: string;
  className?: string;
}

/**
 * Compact body figure with an optional pulsing marker at a given organ's
 * normalized position. The marker is keyed by figure slug
 * (`structure.display.map.markers[figureSlug]`) and placed via the admin
 * PositionEditor, so it always lands in the correct region.
 */
export const OrganPreview: React.FC<Props> = ({ marker, figureSlug, label, className = '' }) => {
  const figure = useAnatomyAtlas((s) => s.figures[figureSlug]);
  const pos = marker ? markerToCanvas(figure, marker) : null;

  return (
    <div className={`flex flex-col items-center ${className}`}>
      <BodySilhouette figureSlug={figureSlug} className="w-full max-w-[140px]" dim>
        {pos && (
          <ellipse
            cx={pos.cx}
            cy={pos.cy}
            rx={pos.r}
            ry={pos.r}
            fill="#ef4444"
            fillOpacity="0.8"
            stroke="#ef4444"
            strokeWidth="2"
            className="animate-pulse"
          >
            <title>{label ?? ''}</title>
          </ellipse>
        )}
      </BodySilhouette>
      {label && (
        <p className="mt-3 text-sm font-black text-gray-700 dark:text-dark-text text-center leading-tight">
          {label}
        </p>
      )}
    </div>
  );
};
