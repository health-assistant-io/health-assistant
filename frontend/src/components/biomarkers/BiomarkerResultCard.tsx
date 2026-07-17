/**
 * BiomarkerResultCard — the body of one biomarker-result card (name, value +
 * unit, status, reference range, date). Extracted from `ObservationView` so the
 * single-record detail overlay (`ObservationDetail`) reuses the exact same
 * rendering as the browse grid — one source of truth for how a biomarker
 * observation displays.
 *
 * Stateless: the caller wraps it in whatever container/chrome it needs (the
 * browse grid's pickable border, or the detail overlay's plain card).
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Activity, AlertTriangle } from 'lucide-react';
import {
  getFinalStatus,
  getStatusColorClass,
  formatBiomarkerValue,
} from '../../utils/biomarkerUtils';
import type { BiomarkerObservation } from '../../types/biomarker';

export interface BiomarkerResultCardProps {
  b: BiomarkerObservation;
}

export const BiomarkerResultCard: React.FC<BiomarkerResultCardProps> = ({ b }) => {
  const { t } = useTranslation();
  const status = getFinalStatus(b);
  const valueNum = b.value.normalized ?? b.value.raw;

  return (
    <>
      <div className="flex items-center gap-2 mb-2">
        {b.isUnmapped ? (
          <AlertTriangle className="w-4 h-4 text-amber-500 shrink-0" />
        ) : (
          <Activity className="w-4 h-4 text-blue-500 shrink-0" />
        )}
        <span className="font-bold text-sm text-gray-900 dark:text-dark-text truncate">
          {b.displayName || t('instances.unmapped_biomarker', 'Unmapped biomarker')}
        </span>
      </div>

      <div className="flex items-baseline gap-2">
        <span
          className={`text-2xl font-black ${
            status === 'Normal'
              ? 'text-gray-900 dark:text-dark-text'
              : 'text-red-600 dark:text-red-400'
          }`}
        >
          {formatBiomarkerValue(valueNum)}
        </span>
        <span className="text-xs text-gray-400 font-medium">
          {b.unit.normalizedSymbol || b.unit.rawSymbol}
        </span>
        <span
          className={`ml-auto text-[10px] font-bold uppercase tracking-wide rounded px-1.5 py-0.5 ${getStatusColorClass(
            status,
          )}`}
        >
          {status}
        </span>
      </div>

      {b.referenceRange?.displayText && (
        <p className="text-[11px] text-gray-400 mt-2">
          {t('biomarkers.ref_range', 'Ref')}: {b.referenceRange.displayText}
        </p>
      )}
      {b.source?.date && (
        <p className="text-[11px] text-gray-400 mt-1">
          {new Date(b.source.date).toLocaleDateString()}
        </p>
      )}
    </>
  );
};

export default BiomarkerResultCard;
