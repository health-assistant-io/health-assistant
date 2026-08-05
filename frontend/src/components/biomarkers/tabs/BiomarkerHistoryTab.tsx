/**
 * BiomarkerDetail "Observations" tab — the longitudinal results table.
 * Extracted from the inline body so the detail page stays navigable as new
 * tabs are added. Offers:
 *  - header "Log Reading" button + empty-state CTA
 *  - per-row Edit + Delete actions for manually-created records
 *
 * Branches on ``biomarker.value_type``:
 *  - QUANTITY: numeric value badge + unit column.
 *  - STATE: color-coded state pill; unit column hidden (categorical = unitless).
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { ChevronRight, Layers, Plus, Activity, Pencil, Trash2 } from 'lucide-react';
import { formatUnit, formatBiomarkerValue } from '../../../utils/biomarkerUtils';
import type { BiomarkerPrecisionProfile } from '../../../utils/biomarkerUtils';
import type { Biomarker } from '../../../types/biomarker';

interface BiomarkerHistoryTabProps {
  biomarker: Biomarker;
  filteredTrends: any[];
  precisionProfile: BiomarkerPrecisionProfile;
  /** Fired when the user clicks "Log Reading". Parent owns the modal mount. */
  onLogReading?: () => void;
  /** Fired when the user clicks the row's edit button. Parent owns the modal. */
  onEditRecord?: (trendRow: any) => void;
  /** Fired when the user clicks the row's delete button. Parent owns the confirm. */
  onDeleteRecord?: (trendRow: any) => void;
}

export const BiomarkerHistoryTab: React.FC<BiomarkerHistoryTabProps> = ({
  biomarker,
  filteredTrends,
  precisionProfile,
  onLogReading,
  onEditRecord,
  onDeleteRecord,
}) => {
  const { t } = useTranslation();
  const canLogReading = !!onLogReading && !biomarker.is_telemetry;
  const isState = biomarker.value_type === 'state';
  // State observations are unitless — collapse the unit column for STATE rows.
  const showUnitColumn = !isState;

  /** Integration/telemetry rows are managed by their source system — editing
   *  or deleting them from the UI would be silently overwritten on the next
   *  sync, so hide the affordance for those sources. */
  const isRowEditable = (trendRow: any): boolean => {
    if (biomarker.is_telemetry) return false;
    const st = trendRow.source_type;
    return st !== 'integration' && st !== 'telemetry';
  };

  /** Pick the right badge classes for a STATE row's normal/abnormal flag. */
  const stateBadgeClass = (trendRow: any): string => {
    if (trendRow.state_is_normal === true) {
      return 'text-emerald-700 bg-emerald-50 dark:text-emerald-300 dark:bg-emerald-900/30 border-emerald-200 dark:border-emerald-800/50';
    }
    if (trendRow.state_is_normal === false) {
      return 'text-rose-700 bg-rose-50 dark:text-rose-300 dark:bg-rose-900/30 border-rose-200 dark:border-rose-800/50';
    }
    // Unknown / unresolved — neutral.
    return 'text-gray-600 bg-gray-50 dark:text-dark-muted dark:bg-dark-bg border-gray-200 dark:border-dark-border';
  };

  const renderValueCell = (trendRow: any) => {
    if (isState) {
      const label = trendRow.state_display || trendRow.state || trendRow.value || '—';
      return (
        <span className={`inline-flex items-center px-3 py-1 rounded-lg text-sm font-black border ${stateBadgeClass(trendRow)}`}>
          {label}
        </span>
      );
    }
    return (
      <span className="text-sm font-black text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/30 px-3 py-1 rounded-lg">
        {formatBiomarkerValue(trendRow.value, precisionProfile)}
      </span>
    );
  };

  return (
    <div className="animate-in fade-in duration-300 h-full flex flex-col">
      {canLogReading && (
        <div className="flex justify-end px-8 pt-6 pb-2">
          <button
            onClick={onLogReading}
            className="flex items-center space-x-2 px-4 py-2 rounded-xl font-bold text-xs uppercase tracking-widest bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-300 hover:bg-blue-100 dark:hover:bg-blue-900/50 transition-all active:scale-95 border border-blue-100 dark:border-blue-900/40"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>{t('biomarkers.log_reading.button', 'Log Reading')}</span>
          </button>
        </div>
      )}
      <div className="flex-1 overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-100 dark:divide-dark-border">
          <thead className="bg-gray-50/50 dark:bg-dark-bg/50">
            <tr>
              <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('dashboard.config.date_range')}</th>
              <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">
                {isState
                  ? t('biomarkers.state_value', 'State')
                  : t('biomarkers.latest_result')}
              </th>
              {showUnitColumn && (
                <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('biomarkers.standard_unit')}</th>
              )}
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
                  {renderValueCell(trendRow)}
                </td>
                {showUnitColumn && (
                  <td className="px-8 py-5 whitespace-nowrap text-xs text-gray-500 dark:text-dark-muted font-bold">
                    {formatUnit(trendRow.unit)}
                  </td>
                )}
                <td className="px-8 py-5 whitespace-nowrap text-xs text-gray-500 dark:text-dark-text font-medium">
                  <div className="flex items-center gap-2">
                    <span className={`px-2 py-0.5 rounded-full text-[10px] uppercase font-bold tracking-widest ${
                      trendRow.source_type === 'integration' ? 'bg-purple-50 text-purple-600 dark:bg-purple-900/30 dark:text-purple-400' :
                      trendRow.source_type === 'examination' ? 'bg-blue-50 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400' :
                      trendRow.source_type === 'document' ? 'bg-orange-50 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400' :
                      'bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400'
                    }`}>
                      {trendRow.source_type || 'manual'}
                    </span>
                    <span>{trendRow.source_name || trendRow.examination_name || t('biomarkers.log_reading.manual_entry_label', 'Manual Entry')}</span>
                  </div>
                </td>
                <td className="px-8 py-5 whitespace-nowrap text-right text-sm font-medium">
                  <div className="inline-flex items-center gap-1">
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
                    {onEditRecord && isRowEditable(trendRow) && (
                      <button
                        type="button"
                        onClick={() => onEditRecord(trendRow)}
                        className="inline-flex items-center justify-center p-2 text-gray-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-xl transition-colors"
                        title={t('common.edit', { defaultValue: 'Edit' })}
                      >
                        <Pencil className="w-4 h-4" />
                      </button>
                    )}
                    {onDeleteRecord && isRowEditable(trendRow) && (
                      <button
                        type="button"
                        onClick={() => onDeleteRecord(trendRow)}
                        className="inline-flex items-center justify-center p-2 text-gray-400 hover:text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-900/20 rounded-xl transition-colors"
                        title={t('common.delete', { defaultValue: 'Delete' })}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {filteredTrends.length === 0 && (
              <tr>
                <td colSpan={showUnitColumn ? 5 : 4} className="px-8 py-16 text-center">
                  <div className="flex flex-col items-center space-y-4">
                    <div className="w-14 h-14 rounded-full bg-gray-50 dark:bg-dark-bg flex items-center justify-center">
                      <Activity className="w-6 h-6 text-gray-300 dark:text-dark-muted" />
                    </div>
                    <p className="text-xs font-black uppercase tracking-widest text-gray-400 dark:text-dark-muted">
                      {t('biomarkers.no_results')}
                    </p>
                    {canLogReading && (
                      <button
                        onClick={onLogReading}
                        className="flex items-center space-x-2 px-5 py-2.5 rounded-xl font-bold text-sm bg-blue-600 hover:bg-blue-700 text-white shadow-lg shadow-blue-200/50 dark:shadow-none transition-all active:scale-95"
                      >
                        <Plus className="w-4 h-4" />
                        <span>{t('biomarkers.log_reading.empty_cta', 'Log the first reading')}</span>
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
