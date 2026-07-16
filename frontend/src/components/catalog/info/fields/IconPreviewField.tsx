/**
 * IconPreviewField — renders a catalog icon descriptor (`{type: 'lucide' |
 * 'custom_svg', value}`) as the actual glyph via {@link DynamicIcon}, plus a
 * mono caption of the icon name.
 *
 * Used for the concept `icon` field. An optional `color` tints the glyph (the
 * concept's `color` is read by the orchestrator via the descriptor's
 * `colorKey`). Empty/invalid values render a muted dash.
 */
import React from 'react';
import { DynamicIcon } from '../../../ui/DynamicIcon';
import type { IconConfig } from '../../../ui/DynamicIcon';

interface IconPreviewFieldProps {
  value: unknown;
  color?: string | null;
}

function toIconConfig(value: unknown): IconConfig | string | null {
  if (!value) return null;
  if (typeof value === 'string') return value;
  if (
    typeof value === 'object' &&
    'value' in (value as Record<string, unknown>) &&
    typeof (value as Record<string, unknown>).value === 'string'
  ) {
    return value as IconConfig;
  }
  return null;
}

export const IconPreviewField: React.FC<IconPreviewFieldProps> = ({ value, color }) => {
  const config = toIconConfig(value);
  if (!config) {
    return <span className="text-gray-400">—</span>;
  }
  const caption =
    typeof config === 'string' ? config : config.value;
  return (
    <span className="inline-flex items-center gap-2">
      <span className="inline-flex items-center justify-center w-7 h-7 rounded-lg bg-gray-100 dark:bg-gray-700 shrink-0">
        <DynamicIcon
          icon={config}
          className="w-4 h-4"
          color={color ?? undefined}
        />
      </span>
      <span className="font-mono text-xs text-gray-500 dark:text-gray-400">
        {caption}
      </span>
    </span>
  );
};

export default IconPreviewField;
