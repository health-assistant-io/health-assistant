import type { ChipVariant } from '@neuronection/assistant-ui';

export {
  ChipList,
  ChipList as default,
  type ChipVariant,
} from '@neuronection/assistant-ui';

/** Legacy tint map for app-side pills (used by catalog EnumBadgeField). */
export const CHIP_VARIANT_CLASSES: Record<string, string> = {
  neutral: 'bg-gray-100 text-gray-600',
  primary: 'bg-blue-50 text-blue-700',
  info: 'bg-blue-50 text-blue-700',
  success: 'bg-emerald-50 text-emerald-700',
  warning: 'bg-amber-50 text-amber-700',
  danger: 'bg-red-50 text-red-700',
};
