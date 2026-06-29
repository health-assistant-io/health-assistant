import React from 'react';
import type { AnatomyMapMarker } from '../../types/anatomy';
import { BodySilhouette, markerToCanvas } from './BodySilhouette';
import { useAnatomyAtlas } from './atlas';

export interface BodyMapMarkerSpec {
  marker: AnatomyMapMarker;
  /** Structure slug — passed to onSelect when the marker is clicked. */
  slug?: string;
  label?: string;
  variant: 'selected' | 'highlight';
}

interface Props {
  figureSlug: string;
  markers?: BodyMapMarkerSpec[];
  selectedSlug?: string;
  onSelect?: (slug: string) => void;
  onHover?: (slug: string | null) => void;
  className?: string;
}

export const BodyMapSVG: React.FC<Props> = ({
  figureSlug,
  markers = [],
  selectedSlug,
  onSelect,
  onHover,
  className = '',
}) => {
  const figure = useAnatomyAtlas((s) => s.figures[figureSlug]);

  return (
    <BodySilhouette figureSlug={figureSlug} className={`w-full max-w-[230px] ${className}`} overlayInteractive>
      {markers.map((spec, idx) => {
        const { cx, cy, r } = markerToCanvas(figure, spec.marker);
        const isSelected = spec.variant === 'selected' || spec.slug === selectedSlug;
        const fill = isSelected ? '#3b82f6' : '#93c5fd';
        const stroke = isSelected ? '#1d4ed8' : '#3b82f6';
        const clickable = !!spec.slug && !!onSelect;
        return (
          <ellipse
            key={`${spec.slug ?? idx}-${idx}`}
            cx={cx}
            cy={cy}
            rx={r}
            ry={r}
            fill={fill}
            fillOpacity={isSelected ? 0.85 : 0.6}
            stroke={stroke}
            strokeWidth={isSelected ? 2.5 : 1.5}
            className={`${isSelected ? 'animate-pulse' : ''} ${clickable ? 'cursor-pointer' : ''}`}
            style={{ pointerEvents: clickable ? 'all' : 'none' }}
            onClick={clickable ? () => onSelect!(spec.slug!) : undefined}
            onMouseEnter={spec.slug && onHover ? () => onHover(spec.slug!) : undefined}
            onMouseLeave={onHover ? () => onHover(null) : undefined}
          >
            {spec.label && <title>{spec.label}</title>}
          </ellipse>
        );
      })}
    </BodySilhouette>
  );
};
