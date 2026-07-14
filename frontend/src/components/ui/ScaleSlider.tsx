/**
 * ScaleSlider — a bounded number rendered as a green→red gradient slider with
 * a synced numeric input.
 *
 * Used by `DynamicMetadataForm` for any `number` field that declares both
 * `min` and `max` (the natural "scale"/"intensity" case), and by the clinical-
 * event occurrences panel. Unbounded numbers stay plain inputs.
 *
 * The track carries a fixed green→yellow→red gradient (communicating the
 * severity axis at a glance); the thumb border + the input border shift colour
 * to match the current value's position (green = low, red = high) via a hue
 * interpolation. The slider and the typed input stay bidirectionally in sync,
 * and the input clamps to ``[min, max]`` on blur. An empty value is allowed
 * (clears the field) and renders with no colour accent.
 */
import React, { useCallback } from 'react';

export interface ScaleSliderProps {
  /** Current value. ``''`` / ``undefined`` / ``null`` = empty. */
  value: number | '' | null | undefined;
  onChange: (value: number | '') => void;
  min: number;
  max: number;
  /** Step (default 1). */
  step?: number;
  /** Optional low-end caption (e.g. "Mild"). */
  lowLabel?: string;
  /** Optional high-end caption (e.g. "Severe"). */
  highLabel?: string;
  /** Show the numeric input alongside the slider (default true). */
  showInput?: boolean;
  disabled?: boolean;
  className?: string;
}

/**
 * Map a 0..1 position to an HSL colour along the green→yellow→red axis.
 * 0 → green (hue ~142), 0.5 → yellow-green (hue ~71), 1 → red (hue 0).
 * Saturation/lightness kept constant for a clean, accessible gradient.
 */
function positionColor(p: number): string {
  const clamped = Math.max(0, Math.min(1, p));
  const hue = 142 * (1 - clamped);
  return `hsl(${hue.toFixed(0)}, 72%, 48%)`;
}

export const ScaleSlider: React.FC<ScaleSliderProps> = ({
  value,
  onChange,
  min,
  max,
  step = 1,
  lowLabel,
  highLabel,
  showInput = true,
  disabled = false,
  className = '',
}) => {
  const span = max - min;
  const numericValue =
    value === '' || value === null || value === undefined || Number.isNaN(value as number)
      ? null
      : Number(value);
  const position = numericValue === null ? 0 : (numericValue - min) / span;
  const thumbColor = numericValue === null ? '#9ca3af' : positionColor(position);

  const sliderValue = numericValue === null ? min : numericValue;

  const handleSlider = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const n = Number(e.target.value);
      onChange(Number.isNaN(n) ? '' : n);
    },
    [onChange],
  );

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const raw = e.target.value;
      if (raw === '') {
        onChange('');
        return;
      }
      const n = Number(raw);
      onChange(Number.isNaN(n) ? '' : n);
    },
    [onChange],
  );

  const handleInputBlur = useCallback(() => {
    if (numericValue === null) return;
    // Clamp into range on blur so a typed out-of-bounds value snaps back
    // rather than silently persisting.
    const clamped = Math.max(min, Math.min(max, numericValue));
    if (clamped !== numericValue) onChange(clamped);
  }, [numericValue, min, max, onChange]);

  const showLabels = Boolean(lowLabel || highLabel);

  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <div className="flex items-center gap-3">
        <input
          type="range"
          className="scale-slider flex-1"
          min={min}
          max={max}
          step={step}
          value={sliderValue}
          disabled={disabled}
          onChange={handleSlider}
          style={{ '--scale-thumb-color': thumbColor } as React.CSSProperties}
          aria-label="Scale"
        />
        {showInput && (
          <input
            type="number"
            min={min}
            max={max}
            step={step}
            value={value ?? ''}
            disabled={disabled}
            onChange={handleInputChange}
            onBlur={handleInputBlur}
            className="w-16 text-center font-bold rounded-lg border-2 bg-white dark:bg-dark-surface text-gray-900 dark:text-dark-text focus:outline-none focus:ring-2 focus:ring-blue-500/30 px-2 py-1.5"
            style={{ borderColor: thumbColor }}
          />
        )}
      </div>
      {showLabels && (
        <div className="flex justify-between text-[9px] font-bold uppercase tracking-wider opacity-50">
          <span style={{ color: positionColor(0) }}>{lowLabel}</span>
          <span style={{ color: positionColor(1) }}>{highLabel}</span>
        </div>
      )}
    </div>
  );
};

/** Exported for consumers that want to mirror the colour logic (e.g. a badge
 *  elsewhere showing the same value). */
export const scaleColorForValue = (
  min: number,
  max: number,
  value: number | '' | null | undefined,
): string => {
  if (value === '' || value === null || value === undefined) return '#9ca3af';
  const n = Number(value);
  if (Number.isNaN(n)) return '#9ca3af';
  return positionColor((n - min) / (max - min));
};
