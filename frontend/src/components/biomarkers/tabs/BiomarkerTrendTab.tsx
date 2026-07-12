/**
 * BiomarkerDetail "Longitudinal Trend" tab — the primary chart + its toolbar
 * (time range, aggregation bucket, chart type, grid/reference/spike overlays).
 *
 * Time range + aggregation are lifted to the parent (they drive the shared
 * data fetch + filteredTrends used by the KPI strip and the history table);
 * chart-only preferences (chart type, grid, spikes) live here.
 */
import React, { useState } from 'react';
import { TrendingUp, Layers, Box, Activity, Calendar } from 'lucide-react';
import LineChart from '../../charts/LineChart';
import { InfoTooltip } from '../../ui/InfoTooltip';
import { useSettingsStore } from '../../../store/slices/settingsSlice';
import type { Biomarker } from '../../../types/biomarker';
import {
  TIME_RANGES,
  AGGREGATION_OPTIONS,
  type TimePeriod,
  type AggregationBucket,
} from '../../../config/timeRanges';

interface BiomarkerTrendTabProps {
  biomarker: Biomarker;
  filteredTrends: any[];
  dateRange: TimePeriod;
  setDateRange: (r: TimePeriod) => void;
  aggregation: AggregationBucket | null;
  setAggregation: (a: AggregationBucket | null) => void;
}

export const BiomarkerTrendTab: React.FC<BiomarkerTrendTabProps> = ({
  biomarker,
  filteredTrends,
  dateRange,
  setDateRange,
  aggregation,
  setAggregation,
}) => {
  const { showReferenceRanges, setShowReferenceRanges } = useSettingsStore();

  // Chart-only display preferences (local to this tab).
  const [chartType, setChartType] = useState<'line' | 'area' | 'bar'>('line');
  const [showGrid, setShowGrid] = useState(true);
  const [showSpikes, setShowSpikes] = useState(true);

  return (
    <div className="p-6 sm:p-8 animate-in fade-in duration-300">
      <div className="flex flex-wrap items-center gap-3 mb-4">
          <div className="flex items-center space-x-2">
            <div className="flex bg-gray-100 dark:bg-dark-bg p-1 rounded-xl border border-gray-200 dark:border-dark-border">
              {TIME_RANGES.map(range => (
                <button
                  key={range.id}
                  onClick={() => setDateRange(range.id)}
                  className={`px-3 py-1.5 text-[10px] font-black rounded-lg transition-all ${dateRange === range.id ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                >
                  {range.shortLabel}
                </button>
              ))}
            </div>

            {biomarker.is_telemetry && aggregation && (
              <div className="flex items-center ml-2 bg-gray-100 dark:bg-dark-bg p-1 rounded-xl border border-gray-200 dark:border-dark-border">
                <select
                  value={aggregation}
                  onChange={(e) => setAggregation(e.target.value as AggregationBucket)}
                  className="bg-transparent text-[10px] font-black text-gray-700 dark:text-gray-300 outline-none cursor-pointer pl-2 pr-6 py-1.5 appearance-none"
                  style={{ backgroundImage: 'url("data:image/svg+xml;charset=US-ASCII,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%22292.4%22%20height%3D%22292.4%22%3E%3Cpath%20fill%3D%22%239CA3AF%22%20d%3D%22M287%2069.4a17.6%2017.6%200%200%200-13-5.4H18.4c-5%200-9.3%201.8-12.9%205.4A17.6%2017.6%200%200%200%200%2082.2c0%205%201.8%209.3%205.4%2012.9l128%20127.9c3.6%203.6%207.8%205.4%2012.8%205.4s9.2-1.8%2012.8-5.4L287%2095c3.5-3.5%205.4-7.8%205.4-12.8%200-5-1.9-9.2-5.5-12.8z%22%2E%3C%2Fsvg%3E")', backgroundRepeat: 'no-repeat', backgroundPosition: 'right .5rem top 50%', backgroundSize: '.65rem auto' }}
                >
                  {AGGREGATION_OPTIONS.map(opt => (
                    <option key={opt.id} value={opt.id}>{opt.label} avg</option>
                  ))}
                </select>
              </div>
            )}

            <InfoTooltip
              className="p-1"
              content={biomarker.is_telemetry
                ? "High-frequency telemetry data is automatically aggregated into dynamic time buckets (e.g. 15-minute, 1-hour, or 1-day averages) based on the selected time range to ensure fast loading."
                : "Standard clinical FHIR data displays every recorded data point exactly as it was measured without aggregation."}
            />
          </div>

          <div className="flex items-center space-x-1 bg-gray-100 dark:bg-dark-bg p-1 rounded-xl border border-gray-200 dark:border-dark-border">
            {[
              { id: 'line', icon: TrendingUp },
              { id: 'area', icon: Layers },
              { id: 'bar', icon: Box },
            ].map(type => (
              <button
                key={type.id}
                onClick={() => setChartType(type.id as 'line' | 'area' | 'bar')}
                className={`p-1.5 rounded-lg transition-all ${chartType === type.id ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                title={type.id.charAt(0).toUpperCase() + type.id.slice(1)}
              >
                <type.icon className="w-3.5 h-3.5" />
              </button>
            ))}
          </div>

          <button
            onClick={() => setShowGrid(!showGrid)}
            className={`p-2 rounded-xl transition-all border ${showGrid ? 'bg-blue-50 dark:bg-blue-900/20 border-blue-100 text-blue-600' : 'bg-white dark:bg-dark-surface border-gray-100 text-gray-400'}`}
            title="Toggle Grid"
          >
            <Layers className="w-4 h-4" />
          </button>

          {(biomarker.reference_range_min != null || biomarker.reference_range_max != null) && (
            <button
              onClick={() => setShowReferenceRanges(!showReferenceRanges)}
              className={`p-2 rounded-xl transition-all border ${showReferenceRanges ? 'bg-emerald-50 dark:bg-emerald-900/20 border-emerald-100 text-emerald-600' : 'bg-white dark:bg-dark-surface border-gray-100 text-gray-400'}`}
              title="Toggle Reference Range Area"
            >
              <Box className="w-4 h-4" />
            </button>
          )}

          {biomarker.is_telemetry && (
            <button
              onClick={() => setShowSpikes(!showSpikes)}
              className={`p-2 rounded-xl transition-all border ${showSpikes ? 'bg-rose-50 dark:bg-rose-900/20 border-rose-100 text-rose-600' : 'bg-white dark:bg-dark-surface border-gray-100 text-gray-400'}`}
              title="Toggle Min/Max Spikes"
            >
              <Activity className="w-4 h-4" />
            </button>
          )}
        </div>

      <div className="h-[400px]">
        {filteredTrends.length > 0 ? (
          <LineChart
            data={filteredTrends.map(trendPoint => ({
              name: new Date(trendPoint.date).toLocaleDateString(),
              tooltipLabel: new Date(trendPoint.date).toLocaleString(undefined, {
                year: 'numeric',
                month: 'short',
                day: 'numeric',
                hour: biomarker.is_telemetry ? 'numeric' : undefined,
                minute: biomarker.is_telemetry ? '2-digit' : undefined,
              }),
              value: trendPoint.value,
              min_value: trendPoint.min_value,
              max_value: trendPoint.max_value,
              range: (trendPoint.min_value !== undefined && trendPoint.max_value !== undefined) ? [trendPoint.min_value, trendPoint.max_value] : undefined,
            }))}
            dataKey="value"
            xAxisKey="name"
            color="#3b82f6"
            referenceRange={{
              min: biomarker.reference_range_min,
              max: biomarker.reference_range_max,
            }}
            showReferenceLines={showReferenceRanges}
            chartType={chartType}
            showGrid={showGrid}
            showSpikes={showSpikes && biomarker.is_telemetry}
            showBrush={true}
            interactiveZoom={true}
            height="100%"
          />
        ) : (
          <div className="h-full flex flex-col items-center justify-center text-center opacity-40">
            <Calendar className="w-12 h-12 mb-4 text-gray-300" />
            <p className="font-bold text-gray-500 uppercase tracking-widest text-sm">No historical data available</p>
          </div>
        )}
      </div>
    </div>
  );
};
