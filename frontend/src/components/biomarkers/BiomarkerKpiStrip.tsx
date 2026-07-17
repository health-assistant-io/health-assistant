/**
 * BiomarkerKpiStrip — the 4-card summary strip (Latest Result, Clinical
 * Reference, Avg Overall, Total Records) shown above the tabs on the
 * BiomarkerDetail page. Extracted so the observation overlay renders the same
 * at-a-glance summary instead of a bare chart — one source of truth for the
 * KPI layout.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import {
  formatBiomarkerValue,
  formatUnit,
  type BiomarkerPrecisionProfile,
} from '../../utils/biomarkerUtils';
import type { Biomarker } from '../../types/biomarker';

export interface BiomarkerKpiStripProps {
  biomarker: Biomarker;
  trends: any[];
  precisionProfile: BiomarkerPrecisionProfile;
}

export const BiomarkerKpiStrip: React.FC<BiomarkerKpiStripProps> = ({
  biomarker,
  trends,
  precisionProfile,
}) => {
  const { t } = useTranslation();
  const latest = trends.length > 0 ? trends[trends.length - 1] : null;
  const avg = trends.length > 0 ? trends.reduce((a, b) => a + b.value, 0) / trends.length : null;

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
      <div className="bg-white dark:bg-dark-surface p-4 rounded-2xl border border-gray-100 dark:border-dark-border shadow-sm">
        <p className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-1">
          {t('biomarkers.latest_result')}
        </p>
        <div className="flex items-baseline space-x-1">
          <span className="text-xl font-black text-gray-900 dark:text-dark-text">
            {latest ? formatBiomarkerValue(latest.value, precisionProfile) : '--'}
          </span>
          <span className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase">
            {latest
              ? formatUnit(latest.unit)
              : biomarker.preferred_unit_symbol
                ? formatUnit(biomarker.preferred_unit_symbol)
                : ''}
          </span>
        </div>
      </div>

      <div className="bg-white dark:bg-dark-surface p-4 rounded-2xl border border-gray-100 dark:border-dark-border shadow-sm">
        <p className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-1">
          {t('biomarkers.clinical_reference')}
        </p>
        <div className="flex items-baseline">
          <span className="text-sm font-bold text-blue-600 dark:text-blue-400 font-mono leading-none">
            {biomarker.reference_range_min != null || biomarker.reference_range_max != null
              ? `${biomarker.reference_range_min ?? '0'} - ${biomarker.reference_range_max ?? '∞'}`
              : 'undefined'}
          </span>
        </div>
      </div>

      <div className="bg-white dark:bg-dark-surface p-4 rounded-2xl border border-gray-100 dark:border-dark-border shadow-sm">
        <p className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-1">
          {t('biomarkers.avg_overall')}
        </p>
        <div className="flex items-baseline">
          <span className="text-xl font-black text-gray-700 dark:text-dark-text">
            {avg !== null ? avg.toFixed(1) : '--'}
          </span>
        </div>
      </div>

      <div className="bg-white dark:bg-dark-surface p-4 rounded-2xl border border-gray-100 dark:border-dark-border shadow-sm">
        <p className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-1">
          {t('biomarkers.total_records')}
        </p>
        <div className="flex items-baseline">
          <span className="text-xl font-black text-gray-700 dark:text-dark-text">{trends.length}</span>
          <span className="ml-1 text-[10px] font-bold text-gray-400 uppercase">
            {t('biomarkers.tests')}
          </span>
        </div>
      </div>
    </div>
  );
};

export default BiomarkerKpiStrip;
