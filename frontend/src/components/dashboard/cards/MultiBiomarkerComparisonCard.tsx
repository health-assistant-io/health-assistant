import React from 'react';
import { useTranslation } from 'react-i18next';
import { X, GitCompareArrows, Info } from 'lucide-react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, ReferenceLine
} from 'recharts';
import { formatUnit, formatBiomarkerValue } from '../../../utils/biomarkerUtils';
import { useBiomarkerPrecisionProfile } from '../../../hooks/useBiomarkerPrecision';
import { SearchableBiomarkerSelect } from '../shared/SearchableBiomarkerSelect';
import { BiomarkerInfoModal } from '../shared/BiomarkerInfoModal';
import { CardTitle } from '../shared/CardTitle';

const COLORS = ['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899'];

export const MultiBiomarkerComparisonCard = React.forwardRef((props: any, ref: any) => {
  const { t } = useTranslation();
  const precisionProfile = useBiomarkerPrecisionProfile();
  const { id, isEditMode, availableBiomarkers, trendsData, config, onUpdateConfig, onRemove, style, className, onMouseDown, onMouseUp, onTouchEnd, children } = props;
  const [selectedInfo, setSelectedInfo] = React.useState<any>(null);

  const selectedBiomarkers: string[] = config?.biomarkers || [];

  const series = React.useMemo(() => {
    if (!trendsData || selectedBiomarkers.length === 0) return { data: [] as any[], slugs: [] as any[] };

    const slugs: any[] = [];
    const rawData: Record<string, Array<{ date: string; value: number }>> = {};

    // Pass 1: collect raw values and metadata per biomarker
    for (const slug of selectedBiomarkers.slice(0, 4)) {
      let points: any[] | undefined = trendsData[slug];
      if (!points || points.length === 0) {
        const key = Object.keys(trendsData).find(k => k === slug || k.toLowerCase() === slug.toLowerCase());
        if (key) points = trendsData[key];
      }
      if (!points || points.length === 0) continue;

      const name = points[0]?.name || slug;
      const unit = points[0]?.unit || '';
      const refMin = points[0]?.reference_range_min;
      const refMax = points[0]?.reference_range_max;
      const hasRefRange = refMin != null && refMax != null && refMax > refMin;

      const values = points
        .map((p: any) => ({ date: p.date, value: p.value }))
        .filter((v: any) => v.value != null && isFinite(v.value));
      if (values.length === 0) continue;

      const autoMin = Math.min(...values.map((v: any) => v.value));
      const autoMax = Math.max(...values.map((v: any) => v.value));

      slugs.push({ slug, name, unit, min: refMin, max: refMax, autoMin, autoMax, hasRefRange });
      rawData[slug] = values;
    }

    // Pass 2: build merged chart data, normalizing each series to 0-100% scale
    const dateMap = new Map<string, Record<string, number | null>>();
    for (const meta of slugs) {
      const values = rawData[meta.slug];
      for (const { date, value } of values) {
        if (!dateMap.has(date)) dateMap.set(date, {});
        const entry = dateMap.get(date)!;
        let normalized: number | null = null;
        if (meta.hasRefRange) {
          normalized = ((value - meta.min) / (meta.max - meta.min)) * 100;
        } else if (meta.autoMax > meta.autoMin) {
          normalized = ((value - meta.autoMin) / (meta.autoMax - meta.autoMin)) * 100;
        }
        entry[meta.slug] = normalized;
      }
    }

    const data = Array.from(dateMap.entries())
      .sort((a, b) => new Date(a[0]).getTime() - new Date(b[0]).getTime())
      .map(([date, values]) => ({ date, ...values }));

    return { data, slugs };
  }, [trendsData, selectedBiomarkers]);

  return (
    <div
      ref={ref}
      style={{ ...style, zIndex: isEditMode ? 100 : (style?.zIndex || 1) }}
      className={`${className || ''} bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-6 flex flex-col relative group ${isEditMode ? '' : 'overflow-hidden'}`}
      onMouseDown={onMouseDown}
      onMouseUp={onMouseUp}
      onTouchEnd={onTouchEnd}
    >
      {isEditMode && onRemove && (
        <button
          onClick={(e) => { e.stopPropagation(); onRemove(id); }}
          className="absolute -top-2 -right-2 bg-red-500 text-white rounded-full p-1 shadow-lg opacity-0 group-hover:opacity-100 transition-opacity z-[60] hover:bg-red-600 active:scale-95"
        >
          <X className="w-3 h-3" />
        </button>
      )}

      <div className="flex items-center justify-between mb-3">
        <CardTitle
          to="/analytics/correlative"
          title={t('dashboard.cards.multi_biomarker_comparison', { defaultValue: 'Biomarker Comparison' })}
          titleClassName="text-sm font-black text-gray-900 dark:text-dark-text tracking-tight"
          icon={
            <div className="p-1.5 bg-purple-50 dark:bg-purple-900/30 rounded-lg">
              <GitCompareArrows className="w-4 h-4 text-purple-500" />
            </div>
          }
        />
                 <button onClick={() => setSelectedInfo({ info: t('dashboard.cards.multi_biomarker_comparison_info', { defaultValue: 'Each biomarker is normalized to **% of its reference range** (0% = at minimum, 100% = at maximum). This lets you compare trends across biomarkers with different units.' }), name: t('dashboard.cards.multi_biomarker_comparison', { defaultValue: 'Biomarker Comparison' }) })} className="p-1.5 text-blue-400 hover:text-blue-600 transition-colors">
          <Info className="w-3.5 h-3.5" />
        </button>
      </div>

      {(isEditMode || selectedBiomarkers.length === 0) && (
        <div className="mb-3 nodrag" onMouseDown={e => e.stopPropagation()}>
          <SearchableBiomarkerSelect
            value={selectedBiomarkers}
            options={availableBiomarkers || []}
            onChange={(vals: string[]) => onUpdateConfig(id, { ...config, biomarkers: vals })}
            multiple
            placeholder={t('dashboard.config.select_biomarkers', { defaultValue: 'Select biomarkers to compare' })}
            discreet
          />
        </div>
      )}

      {series.data.length > 0 && series.slugs.length > 0 ? (
        <div className="flex-1 min-h-[160px] nodrag">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={series.data} margin={{ top: 5, right: 10, left: -15, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-gray-100 dark:stroke-dark-border" />
              <XAxis dataKey="date" tick={{ fontSize: 9, fill: 'currentColor' }} className="text-gray-400" tickFormatter={(d) => { try { return new Date(d).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }); } catch { return d; } }} />
              <YAxis tick={{ fontSize: 9, fill: 'currentColor' }} className="text-gray-400" domain={[-50, 150]} ticks={[0, 50, 100]} unit="%" width={40} />
              <Tooltip
                contentStyle={{ fontSize: 11, borderRadius: 12, border: 'none', boxShadow: '0 4px 24px rgba(0,0,0,0.1)' }}
                labelFormatter={(d) => { try { return new Date(d as string).toLocaleDateString(); } catch { return d; } }}
                formatter={(val: any, _name: any, item: any) => {
                  const dataKey = item?.dataKey as string | undefined;
                  const meta = series.slugs.find((s: any) => s.slug === dataKey);
                  const v = typeof val === 'number' ? val : null;
                  if (v == null || !meta) return ['--', meta?.name ?? ''];
                  let real: number | null = null;
                  if (meta.hasRefRange && meta.min != null && meta.max != null) {
                    real = meta.min + (v / 100) * (meta.max - meta.min);
                  } else if (meta.autoMin != null && meta.autoMax != null) {
                    real = meta.autoMin + (v / 100) * (meta.autoMax - meta.autoMin);
                  }
                  if (real == null) return ['--', meta.name];
                  return [`${formatBiomarkerValue(real, precisionProfile)} ${formatUnit(meta.unit)}`, meta.name];
                }}
              />
              <ReferenceLine y={0} stroke="#3b82f6" strokeDasharray="2 2" strokeOpacity={0.4} />
              <ReferenceLine y={100} stroke="#ef4444" strokeDasharray="2 2" strokeOpacity={0.4} />
              <ReferenceLine y={50} stroke="#10b981" strokeDasharray="2 2" strokeOpacity={0.3} />
              {series.slugs.map((s, i) => (
                <Line key={s.slug} type="monotone" dataKey={s.slug} stroke={COLORS[i % COLORS.length]} strokeWidth={2} dot={{ r: 2 }} connectNulls name={s.name} />
              ))}
              <Legend wrapperStyle={{ fontSize: 10 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="flex-1 flex items-center justify-center text-gray-400 text-sm py-8">
          {selectedBiomarkers.length === 0
            ? t('dashboard.config.select_biomarkers', { defaultValue: 'Select biomarkers to compare' })
            : t('common.no_data', { defaultValue: 'No data available' })}
        </div>
      )}

      {selectedInfo && (
        <BiomarkerInfoModal info={selectedInfo.info} name={selectedInfo.name} onClose={() => setSelectedInfo(null)} />
      )}
      {children}
    </div>
  );
});
