/**
 * BiomarkerDetail "Technical Metadata" tab — the read-only identity/normalization
 * fields (category, coding system + code, preferred unit, aliases). Moved out of
 * the right sidebar so the detail page is purely exploratory; the catalog form
 * owns editing of these fields.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { formatUnit } from '../../../utils/biomarkerUtils';
import type { Biomarker } from '../../../types/biomarker';

interface BiomarkerTechnicalTabProps {
  biomarker: Biomarker;
  /** Latest trend unit, used as a fallback when no preferred unit is set. */
  fallbackUnit?: string;
}

export const BiomarkerTechnicalTab: React.FC<BiomarkerTechnicalTabProps> = ({
  biomarker,
  fallbackUnit,
}) => {
  const { t } = useTranslation();

  return (
    <div className="p-8 animate-in fade-in duration-300">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="bg-gray-50 dark:bg-dark-bg/30 rounded-2xl p-5 border border-gray-100 dark:border-dark-border">
          <p className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-1">
            {t('biomarkers.perspectives.technical') || 'Category'}
          </p>
          <p className="text-sm font-black text-gray-700 dark:text-dark-text">
            {biomarker.category || 'Uncategorized'}
          </p>
        </div>

        <div className="bg-gray-50 dark:bg-dark-bg/30 rounded-2xl p-5 border border-gray-100 dark:border-dark-border">
          <p className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-1">
            {t('biomarkers.standard_unit')}
          </p>
          <p className="text-sm font-black text-gray-700 dark:text-dark-text">
            {biomarker.preferred_unit_symbol
              ? formatUnit(biomarker.preferred_unit_symbol)
              : fallbackUnit
                ? formatUnit(fallbackUnit)
                : '--'}
          </p>
        </div>

        <div className="bg-gray-50 dark:bg-dark-bg/30 rounded-2xl p-5 border border-gray-100 dark:border-dark-border">
          <p className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-1">
            {(biomarker.coding_system || 'custom').toUpperCase()}
          </p>
          <p className="text-[11px] font-mono font-black bg-white dark:bg-dark-surface px-2 py-1 rounded border border-gray-200 dark:border-dark-border shadow-sm w-fit">
            {biomarker.code || biomarker.slug}
          </p>
        </div>

        <div className="bg-gray-50 dark:bg-dark-bg/30 rounded-2xl p-5 border border-gray-100 dark:border-dark-border">
          <p className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-1">
            {t('biomarkers.tests')}
          </p>
          <p className="text-sm font-black text-gray-700 dark:text-dark-text">
            {biomarker.is_telemetry ? 'IoT Telemetry' : 'FHIR Observation'}
          </p>
        </div>
      </div>

      {biomarker.aliases && biomarker.aliases.length > 0 && (
        <div className="mt-6 pt-6 border-t border-gray-100 dark:border-white/5">
          <p className="text-xs text-gray-500 font-bold uppercase tracking-tighter block mb-3">
            {t('biomarkers.known_aliases')}
          </p>
          <div className="flex flex-wrap gap-2">
            {biomarker.aliases.map((alias, idx) => (
              <div
                key={idx}
                className="px-2.5 py-1 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-lg text-[10px] font-black text-blue-600 dark:text-blue-400 uppercase tracking-tight shadow-sm hover:scale-105 transition-transform cursor-default"
              >
                {alias}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
