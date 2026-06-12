import React, { useMemo } from 'react';
import { 
  LineChart, 
  Line, 
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip, 
  ResponsiveContainer, 
  Legend,
  ReferenceArea
} from 'recharts';
import { useSettingsStore } from '../../store/slices/settingsSlice';

interface DataPoint {
  date: string;
  value: number;
  normalizedValue: number;
  originalValue: number;
  unit: string;
}

interface Dataset {
  label: string;
  data: DataPoint[];
  color: string;
  unit: string;
}

interface CorrelationChartProps {
  datasets: Dataset[];
  height?: number | string;
  showReferenceArea?: boolean;
}

const CorrelationChart: React.FC<CorrelationChartProps> = ({
  datasets,
  height = 400,
  showReferenceArea = true,
}) => {
  const theme = useSettingsStore(state => state.theme);
  const isDark = theme === 'dark';

  // Merge all datasets into a single array for Recharts
  // We need to align them by date
  const chartData = useMemo(() => {
    const dateMap: Record<string, any> = {};
    
    datasets.forEach((dataset) => {
      dataset.data.forEach((point) => {
        if (!dateMap[point.date]) {
          dateMap[point.date] = { date: point.date };
        }
        dateMap[point.date][dataset.label] = point.normalizedValue;
        dateMap[point.date][`${dataset.label}_raw`] = point.originalValue;
        dateMap[point.date][`${dataset.label}_unit`] = dataset.unit;
      });
    });

    return Object.values(dateMap).sort((a, b) => 
      new Date(a.date).getTime() - new Date(b.date).getTime()
    ).map(item => ({
      ...item,
      formattedDate: new Date(item.date).toLocaleDateString(undefined, { 
        month: 'short', 
        day: 'numeric',
        year: '2-digit'
      })
    }));
  }, [datasets]);

  const CustomTooltip = ({ active, payload, label }: any) => {
    if (active && payload && payload.length) {
      return (
        <div className="bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border p-4 rounded-xl shadow-xl backdrop-blur-md bg-opacity-95">
          <p className="text-xs font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-3 border-b border-gray-50 dark:border-dark-border pb-2">
            {label}
          </p>
          <div className="space-y-2">
            {payload.map((entry: any, index: number) => (
              <div key={index} className="flex items-center justify-between space-x-8">
                <div className="flex items-center space-x-2">
                  <div 
                    className="w-2 h-2 rounded-full" 
                    style={{ backgroundColor: entry.color }} 
                  />
                  <span className="text-sm font-bold text-gray-700 dark:text-dark-text">
                    {entry.name}
                  </span>
                </div>
                <div className="text-right">
                  <span className="text-sm font-black text-gray-900 dark:text-white">
                    {entry.payload[`${entry.name}_raw`]}
                  </span>
                  <span className="text-[10px] font-bold text-gray-400 ml-1 uppercase">
                    {entry.payload[`${entry.name}_unit`]}
                  </span>
                </div>
              </div>
            ))}
          </div>
          <div className="mt-3 pt-2 border-t border-gray-50 dark:border-dark-border">
            <p className="text-[10px] font-medium text-blue-500 italic">
              * Values normalized to 0-1 scale for comparison
            </p>
          </div>
        </div>
      );
    }
    return null;
  };

  return (
    <div className="w-full h-full min-h-[300px]">
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={chartData} margin={{ top: 20, right: 30, left: 0, bottom: 20 }}>
          <CartesianGrid 
            strokeDasharray="3 3" 
            vertical={false} 
            stroke={isDark ? '#1e293b' : '#f1f5f9'} 
          />
          <XAxis 
            dataKey="formattedDate" 
            stroke={isDark ? '#64748b' : '#94a3b8'} 
            fontSize={11}
            tickLine={false}
            axisLine={false}
            dy={10}
          />
          <YAxis 
            stroke={isDark ? '#64748b' : '#94a3b8'} 
            fontSize={11}
            tickLine={false}
            axisLine={false}
            domain={[0, 1]}
            tickFormatter={(value) => `${(value * 100).toFixed(0)}%`}
          />
          <Tooltip content={<CustomTooltip />} />
          <Legend 
            verticalAlign="top" 
            align="right"
            iconType="circle"
            content={({ payload }) => (
              <div className="flex flex-wrap justify-end gap-4 mb-6">
                {payload?.map((entry: any, index: number) => (
                  <div key={index} className="flex items-center space-x-2">
                    <div 
                      className="w-3 h-3 rounded-full" 
                      style={{ backgroundColor: entry.color }} 
                    />
                    <span className="text-xs font-black text-gray-600 dark:text-dark-text uppercase tracking-wider">
                      {entry.value}
                    </span>
                  </div>
                ))}
              </div>
            )}
          />
          
          {showReferenceArea && (
            <ReferenceArea 
              y1={0.25} 
              y2={0.75} 
              fill={isDark ? "rgba(34, 197, 94, 0.05)" : "rgba(34, 197, 94, 0.03)"} 
              stroke="none"
            />
          )}

          {datasets.map((dataset) => (
            <Line
              key={dataset.label}
              type="monotone"
              dataKey={dataset.label}
              name={dataset.label}
              stroke={dataset.color}
              strokeWidth={3}
              dot={{ r: 4, strokeWidth: 2, fill: isDark ? '#0f172a' : '#fff' }}
              activeDot={{ r: 6, strokeWidth: 0 }}
              connectNulls
              animationDuration={1000}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};

export default CorrelationChart;
