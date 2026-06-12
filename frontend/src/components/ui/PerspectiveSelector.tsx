import React, { useState, useEffect, useCallback, useRef } from 'react';

interface Point {
  x: number;
  y: number;
}

interface PerspectiveSelectorProps {
  imageWidth: number;
  imageHeight: number;
  points: Point[];
  onChange: (points: Point[] | ((prev: Point[]) => Point[])) => void;
  rotation: number;
}

export const PerspectiveSelector: React.FC<PerspectiveSelectorProps> = ({
  imageWidth, imageHeight, points, onChange, rotation
}) => {
  const [activePoint, setActivePoint] = useState<number | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const rectRef = useRef<DOMRect | null>(null);

  const handleMouseDown = (e: React.MouseEvent, idx: number) => {
    e.preventDefault();
    e.stopPropagation();
    if (containerRef.current) {
        rectRef.current = containerRef.current.getBoundingClientRect();
    }
    setActivePoint(idx);
  };

  const handleMouseMove = useCallback((e: MouseEvent) => {
    if (activePoint === null || !rectRef.current) return;

    const rect = rectRef.current;
    
    // In this optimized version, we assume the points are relative to the current orientation
    // because the parent container is already rotated. 
    // We just need to map viewport coordinates to the 0-100 range of the bounding box.
    const nx = Math.max(0, Math.min(100, ((e.clientX - rect.left) / rect.width) * 100));
    const ny = Math.max(0, Math.min(100, ((e.clientY - rect.top) / rect.height) * 100));

    onChange(prev => {
      const next = [...prev];
      next[activePoint] = { x: nx, y: ny };
      return next;
    });
  }, [activePoint, onChange]);

  const handleMouseUp = useCallback(() => {
    setActivePoint(null);
    rectRef.current = null;
  }, []);

  useEffect(() => {
    if (activePoint !== null) {
      window.addEventListener('mousemove', handleMouseMove, { passive: true });
      window.addEventListener('mouseup', handleMouseUp);
      return () => {
        window.removeEventListener('mousemove', handleMouseMove);
        window.removeEventListener('mouseup', handleMouseUp);
      };
    }
  }, [activePoint, handleMouseMove, handleMouseUp]);

  const maskPath = `
    M 0 0 H 100 V 100 H 0 Z
    M ${points[0].x} ${points[0].y}
    L ${points[1].x} ${points[1].y}
    L ${points[2].x} ${points[2].y}
    L ${points[3].x} ${points[3].y}
    Z
  `;

  return (
    <div 
      ref={containerRef}
      className="absolute inset-0 cursor-crosshair select-none touch-none overflow-hidden"
      style={{ willChange: 'contents' }}
    >
      <svg 
        viewBox="0 0 100 100" 
        preserveAspectRatio="none"
        className="w-full h-full overflow-visible pointer-events-none"
        style={{ willChange: 'transform' }}
      >
        <path
          d={maskPath}
          fill="black"
          fillOpacity="0.4"
          fillRule="evenodd"
        />

        <polygon
          points={points.map(p => `${p.x},${p.y}`).join(' ')}
          fill="transparent"
          stroke="#3b82f6"
          strokeWidth="0.5"
          strokeDasharray="1,1"
        />

        {points.map((p, i) => (
          <g key={i} className="pointer-events-auto cursor-move">
            <circle
              cx={p.x}
              cy={p.y}
              r="6"
              fill="transparent"
              onMouseDown={(e: any) => handleMouseDown(e, i)}
            />
            <circle
              cx={p.x}
              cy={p.y}
              r="1.2"
              className="fill-white stroke-blue-500 stroke-[0.3]"
            />
            <circle
              cx={p.x}
              cy={p.y}
              r="0.4"
              className="fill-blue-500"
            />
          </g>
        ))}
      </svg>
    </div>
  );
};
