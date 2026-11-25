/**
 * EnumBadgeField — renders a single enum/coded value as a colored badge, the
 * value resolved through an `options` map (raw value → display label).
 *
 * Used for closed-set fields like allergy `category` (FOOD/MEDICATION/…) and
 * concept `status` (draft/active/retired). An optional `tones` map gives per-
 * value semantic coloring (e.g. active→success, retired→neutral); values
 * without a tone fall back to `defaultTone`.
 */
import React from 'react';
import { ChipList } from '../../../ui/ChipList';
import type { ChipVariant } from '../../../ui/ChipList';

interface EnumBadgeFieldProps {
  value: string | null | undefined;
  /** raw value → display label. Unknown values render the raw value. */
  options: Record<string, string>;
  /** per-value tone override (value → ChipVariant). */
  tones?: Partial<Record<string, ChipVariant>>;
  defaultTone?: ChipVariant;
}

export const EnumBadgeField: React.FC<EnumBadgeFieldProps> = ({
  value,
  options,
  tones,
  defaultTone = 'neutral',
}) => {
  if (value === null || value === undefined || value === '') {
    return <span className="text-gray-400">—</span>;
  }
  const raw = String(value);
  // Case-insensitive option lookup (enum values arrive as serialized .value,
  // which differs in case across enums — AllergyCategory=UPPER, ConceptStatus=lower).
  const label =
    options[raw] ??
    options[raw.toLowerCase()] ??
    options[raw.toUpperCase()] ??
    raw;
  const tone = tones?.[raw] ?? defaultTone;
  return <ChipList items={[label]} variant={tone} />;
};

export default EnumBadgeField;
