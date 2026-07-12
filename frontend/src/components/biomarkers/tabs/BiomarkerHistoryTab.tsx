/**
 * BiomarkerDetail "Observations" tab — the longitudinal results table.
 * Extracted from the inline body so the detail page stays navigable as new
 * tabs are added. Read-only (no inline editing of individual results here).
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { ChevronRight, Layers } from 'lucide-react';
import { formatUnit, formatBiomarkerValue } from '../../../utils/biomarkerUtils';
import type { BiomarkerPrecisionProfile } from '../../../utils/biomarkerUtils';
import type { Biomarker } from '../../../types/biomarker';

interface BiomarkerHistoryTabProps {
  biomarker: Biomarker;
  filteredTrends: any[];
  precisionProfile: BiomarkerPrecisionProfile;
}

export const BiomarkerHistoryTab: React.FC<BiomarkerHistoryTabProps> = ({
  biomarker,
  filteredTrends,
  precisionProfile,
}) => {
  const { t } = useTranslation();

  return (
    <div className="animate-in fade-in duration-300 h-full flex flex-col">
      <div className="flex-1 overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-100 dark:divide-dark-border">
          <thead className="bg-gray-50/50 dark:bg-dark-bg/50">
            <tr>
              <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('dashboard.config.date_range')}</th>
              <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('biomarkers.latest_result')}</th>
              <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('biomarkers.standard_unit')}</th>
              <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('common.source') || 'Source'}</th>
              <th className="px-8 py-4 text-right text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('common.actions') || 'Actions'}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50 dark:divide-dark-border">
            {filteredTrends.map((trendRow, i) => (
              <tr key={i} className="group hover:bg-blue-50/30 dark:hover:bg-blue-900/10 transition-colors">
                <td className="px-8 py-5 whitespace-nowrap text-sm font-bold text-gray-900 dark:text-dark-text">
                  {new Date(trendRow.date).toLocaleString(undefined, {
                    year: 'numeric',
                    month: 'short',
                    day: 'numeric',
                    hour: biomarker?.is_telemetry ? 'numeric' : undefined,
                    minute: biomarker?.is_telemetry ? '2-digit' : undefined,
                  })}
                </td>
                <td className="px-8 py-5 whitespace-nowrap">
                  <span className="text-sm font-black text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/30 px-3 py-1 rounded-lg">
                    {formatBiomarkerValue(trendRow.value, precisionProfile)}
                  </span>
                </td>
                <td className="px-8 py-5 whitespace-nowrap text-xs text-gray-500 dark:text-dark-muted font-bold">
                  {formatUnit(trendRow.unit)}
                </td>
                <td className="px-8 py-5 whitespace-nowrap text-xs text-gray-500 dark:text-dark-text font-medium">
                  <div className="flex items-center gap-2">
                    <span className={`px-2 py-0.5 rounded-full text-[10px] uppercase font-bold tracking-widest ${
                      trendRow.source_type === 'integration' ? 'bg-purple-50 text-purple-600 dark:bg-purple-900/30 dark:text-purple-400' :
                      trendRow.source_type === 'examination' ? 'bg-blue-50 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400' :
                      trendRow.source_type === 'document' ? 'bg-orange-50 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400' :
                      'bg-gray-100 text-gray-500 dark:bg-dark-bg dark:text-dark-muted'
                    }`}>
                      {trendRow.source_type || 'manual'}
                    </span>
                    <span>{trendRow.source_name || trendRow.examination_name || 'Manual Entry'}</span>
                  </div>
                </td>
                <td className="px-8 py-5 whitespace-nowrap text-right text-sm font-medium">
                  {trendRow.source_type === 'integration' && (
                    <Link
                      to={`/settings/integrations/${trendRow.source_id || trendRow.source_name}`}
                      className="inline-flex items-center justify-center p-2 text-purple-600 hover:bg-purple-50 dark:hover:bg-purple-900/20 rounded-xl transition-colors"
                      title="View Integration"
                    >
                      <Layers className="w-4 h-4" />
                    </Link>
                  )}
                  {trendRow.source_type === 'examination' && trendRow.examination_id && (
                    <Link
                      to={`/examinations/${trendRow.examination_id}`}
                      className="inline-flex items-center justify-center p-2 text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-xl transition-colors"
                      title="View Examination"
                    >
                      <ChevronRight className="w-4 h-4" />
                    </Link>
                  )}
                </td>
              </tr>
            ))}
            {filteredTrends.length === 0 && (
              <tr>
                <td colSpan={5} className="px-8 py-20 text-center text-gray-400 dark:text-dark-muted font-bold uppercase tracking-widest text-xs">{t('biomarkers.no_results')}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
