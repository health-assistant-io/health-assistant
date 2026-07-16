/**
 * ColorSwatchField — renders a CSS color string (typically a hex code) as a
 * filled swatch + the mono value + a copy affordance.
 *
 * Used for the concept `color` field. An empty value renders a muted dash.
 */
import React from 'react';
import { CopyButton } from '../../../ui/CopyButton';

interface ColorSwatchFieldProps {
  value: unknown;
}

export const ColorSwatchField: React.FC<ColorSwatchFieldProps> = ({ value }) => {
  const hex = typeof value === 'string' ? value.trim() : '';
  if (!hex) {
    return <span className="text-gray-400">—</span>;
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className="w-4 h-4 rounded-full border border-gray-300 dark:border-gray-600 shrink-0"
        style={{ backgroundColor: hex }}
        aria-label={`Color ${hex}`}
        role="img"
      />
      <span className="font-mono text-xs">{hex}</span>
      <CopyButton value={hex} size={12} />
    </span>
  );
};

export default ColorSwatchField;
