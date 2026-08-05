/**
 * AllowedStatesField — renders a STATE biomarker's `allowed_states[]` array
 * as a compact table (Display / Code / Normal-flag) in the catalog Info tab.
 *
 * Each entry carries `{state_id, state_slug, code, system, display, is_normal,
 * sort_order}` (mirrors the backend BiomarkerCatalogAdapter serialization).
 * The "normal" set is highlighted with a green badge — it's the categorical
 * equivalent of a numeric reference range (analytics computes status as
 * "value in normal_set → Normal else Abnormal").
 *
 * Modeled on {@link RefRangesField} — null/empty array renders a muted dash.
 */
import React from 'react';
import { Check } from 'lucide-react';
import type { AllowedState } from '../../../../types/biomarker';

interface AllowedStatesFieldProps {
  value: unknown;
}

export const AllowedStatesField: React.FC<AllowedStatesFieldProps> = ({ value }) => {
  if (!Array.isArray(value) || value.length === 0) {
    return <span className="text-gray-400">—</span>;
  }
  const states = value as AllowedState[];

  // Sort: normal states first, then by sort_order, then by display name — so
  // the "normal set" reads as a header band at a glance.
  const sorted = [...states].sort((a, b) => {
    const aNormal = a.is_normal ? 0 : 1;
    const bNormal = b.is_normal ? 0 : 1;
    if (aNormal !== bNormal) return aNormal - bNormal;
    const aOrder = a.sort_order ?? Number.MAX_SAFE_INTEGER;
    const bOrder = b.sort_order ?? Number.MAX_SAFE_INTEGER;
    if (aOrder !== bOrder) return aOrder - bOrder;
    return (a.display || '').localeCompare(b.display || '');
  });

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {sorted.map((s) => {
          const isNormal = !!s.is_normal;
          return (
            <span
              key={s.state_id || s.state_slug || s.code}
              title={s.system ? `System: ${s.system}` : undefined}
              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold border ${
                isNormal
                  ? 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-300 dark:border-emerald-800/50'
                  : 'bg-gray-50 text-gray-600 border-gray-200 dark:bg-dark-bg dark:text-dark-muted dark:border-dark-border'
              }`}
            >
              {isNormal && <Check className="w-3 h-3" aria-hidden />}
              <span>{s.display || s.code || s.state_slug}</span>
              {s.code && s.code !== s.display && (
                <span className="font-mono opacity-60">· {s.code}</span>
              )}
            </span>
          );
        })}
      </div>
      <p className="text-[10px] text-gray-400 dark:text-dark-muted italic">
        {sorted.filter((s) => s.is_normal).length} normal · {sorted.length} total
      </p>
    </div>
  );
};

export default AllowedStatesField;
