/**
 * BiomarkerSnapshotCard — the "Patient Snapshot" body (latest result,
 * clinical reference, 6-month average, record count) shown for a biomarker.
 *
 * Single source of truth — rendered both in the right sidebar of the detail
 * page (desktop xl+) and inside the mobile "Snapshot" tab. The outer card
 * wrapper is intentionally NOT included so each caller can frame the content
 * appropriately (sidebar card vs. tab padding).
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowUpRight, ArrowDownRight, Minus } from 'lucide-react';
import {
  formatUnit,
  getStatusColorClass,
  getStatusTextColor,
  formatBiomarkerValue,
  type BiomarkerPrecisionProfile,
} from '../../utils/biomarkerUtils';
import type { AllowedState, Biomarker } from '../../types/biomarker';

export interface BiomarkerSnapshotCardProps {
  biomarker: Biomarker;
  trends: any[];
  precisionProfile: BiomarkerPrecisionProfile;
  interpretation: string;
}

export const BiomarkerSnapshotCard: React.FC<BiomarkerSnapshotCardProps> = ({
  biomarker,
  trends,
  precisionProfile,
  interpretation,
}) => {
  const { t } = useTranslation();

  const latest = trends.length > 0 ? trends[trends.length - 1] : null;
  const previous = trends.length > 1 ? trends[trends.length - 2] : null;
  // Percentage change vs the previous reading — only meaningful for QUANTITY
  // biomarkers with two or more numeric points and a non-zero baseline.
  const deltaPct =
    latest != null &&
    previous != null &&
    typeof latest.value === 'number' &&
    typeof previous.value === 'number' &&
    previous.value !== 0
      ? ((latest.value - previous.value) / Math.abs(previous.value)) * 100
      : null;

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <h4 className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em]">
          {t('biomarkers.patient_snapshot')}
        </h4>
        {trends.length > 0 && (
          <span
            className={`px-2.5 py-1 rounded-lg text-[10px] font-black uppercase tracking-wider border ${getStatusColorClass(
              interpretation,
            )}`}
          >
            {interpretation}
          </span>
        )}
      </div>

      <div>
        <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-2">
          {t('biomarkers.latest_result')}
        </p>
        <div className="flex items-baseline space-x-2">
          <span
            className={`text-4xl font-black tracking-tighter ${
              latest ? getStatusTextColor(interpretation) : 'text-gray-900 dark:text-dark-text'
            }`}
          >
            {latest ? formatBiomarkerValue(latest.value, precisionProfile) : '--'}
          </span>
          <span className="text-sm font-bold text-gray-400 dark:text-dark-muted uppercase">
            {latest
              ? formatUnit(latest.unit)
              : biomarker.preferred_unit_symbol
                ? formatUnit(biomarker.preferred_unit_symbol)
                : ''}
          </span>
        </div>
        {deltaPct !== null && (
          <div
            className="mt-2 inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-black uppercase tracking-wider border bg-gray-50 text-gray-500 border-gray-100 dark:bg-dark-bg dark:text-dark-muted dark:border-dark-border"
            title={t('biomarkers.delta_vs_previous_tooltip', 'Change vs the previous reading')}
          >
            {deltaPct > 0 ? (
              <ArrowUpRight className="w-3 h-3" />
            ) : deltaPct < 0 ? (
              <ArrowDownRight className="w-3 h-3" />
            ) : (
              <Minus className="w-3 h-3" />
            )}
            <span>
              {t('biomarkers.delta_vs_previous', {
                defaultValue: '{{pct}}% vs prev',
                pct: Math.abs(deltaPct).toFixed(1),
              })}
            </span>
          </div>
        )}
      </div>

      <div className="p-5 bg-gray-50 dark:bg-dark-bg/50 rounded-2xl border border-gray-100 dark:border-dark-border shadow-inner">
        <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-2">
          {t('biomarkers.clinical_reference')}
        </p>
        <div className="flex items-baseline space-x-2">
          {biomarker.value_type === 'state' ? (
            <span className="text-xl font-black text-emerald-600 dark:text-emerald-400">
              {(biomarker.allowed_states ?? [])
                .filter((s: AllowedState) => s.is_normal)
                .map((s: AllowedState) => s.display)
                .join(', ') || t('biomarker_catalog.allowed_state_empty', 'No normal set configured')}
            </span>
          ) : (
            <>
              <span
                className={`${
                  biomarker.reference_range_min != null || biomarker.reference_range_max != null
                    ? 'text-xl font-black text-blue-600 dark:text-blue-400 font-mono tracking-tighter'
                    : 'text-sm font-medium text-gray-300 dark:text-dark-muted/30 italic'
                }`}
              >
                {biomarker.reference_range_min != null && biomarker.reference_range_max != null
                  ? `${biomarker.reference_range_min} - ${biomarker.reference_range_max}`
                  : biomarker.reference_range_min != null
                    ? `> ${biomarker.reference_range_min}`
                    : biomarker.reference_range_max != null
                      ? `< ${biomarker.reference_range_max}`
                      : 'undefined'}
              </span>
              {(biomarker.reference_range_min != null || biomarker.reference_range_max != null) && (
                <span className="text-xs font-bold text-gray-400 dark:text-dark-muted">
                  {trends.length > 0 ? formatUnit(trends[0].unit) : ''}
                </span>
              )}
            </>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-6 pt-4 border-t border-gray-50 dark:border-dark-border">
        <div>
          <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-1">
            {t('biomarkers.avg_6mo')}
          </p>
          <p className="text-lg font-black text-gray-700 dark:text-dark-text leading-none">
            {trends.length > 0 && trends.every((tr: any) => typeof tr.value === 'number')
              ? (trends.reduce((a, b) => a + b.value, 0) / trends.length).toFixed(1)
              : '--'}
          </p>
        </div>
        <div>
          <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-1">
            {t('biomarkers.tests')}
          </p>
          <p className="text-lg font-black text-gray-700 dark:text-dark-text leading-none">
            {trends.length}{' '}
            <span className="text-[9px] font-bold text-gray-400 uppercase ml-0.5">Rec.</span>
          </p>
        </div>
      </div>
    </div>
  );
};

export default BiomarkerSnapshotCard;
