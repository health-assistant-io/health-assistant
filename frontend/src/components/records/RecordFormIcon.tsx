import React from 'react';
import type { LucideIcon } from 'lucide-react';

export type RecordFormTone = 'rose' | 'blue' | 'emerald' | 'slate';

const BADGE: Record<RecordFormTone, string> = {
  rose: 'bg-rose-50 dark:bg-rose-900/30',
  blue: 'bg-blue-50 dark:bg-blue-900/30',
  emerald: 'bg-emerald-50 dark:bg-emerald-900/30',
  slate: 'bg-slate-100 dark:bg-slate-800/40',
};

const ICON: Record<RecordFormTone, string> = {
  rose: 'text-rose-600 dark:text-rose-400',
  blue: 'text-blue-600 dark:text-blue-400',
  emerald: 'text-emerald-600 dark:text-emerald-400',
  slate: 'text-slate-600 dark:text-slate-300',
};

/** The colored record-type badge used in `PanelModal`'s `headerIcon` slot. */
export const RecordFormIcon: React.FC<{ icon: LucideIcon; tone?: RecordFormTone }> = ({
  icon: Icon,
  tone = 'blue',
}) => (
  <span className={`rounded-xl p-2 ${BADGE[tone]}`}>
    <Icon aria-hidden="true" className={`h-5 w-5 ${ICON[tone]}`} />
  </span>
);

export default RecordFormIcon;
