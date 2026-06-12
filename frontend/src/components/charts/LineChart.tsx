import React, { useMemo, useState, useEffect, useCallback } from 'react';
import { 
  LineChart as RechartsLineChart, 
  Line, 
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip, 
  ResponsiveContainer, 
  ReferenceLine, 
  Label, 
  ReferenceArea,
  Brush
} from 'recharts';
import { useSettingsStore } from '../../store/slices/settingsSlice';
import { RefreshCw, ZoomIn } from 'lucide-react';

interface LineChartProps {
  data: Array<{ name: string; value: number }>;
  dataKey?: string;
  xAxisKey?: string;
  color?: string;
  height?: number | string;
  showLegend?: boolean;
  referenceRange?: {
    min?: number | null;
    max?: number | null;
  };
  showReferenceLines?: boolean;
  chartType?: 'line' | 'area' | 'bar';
  showGrid?: boolean;
  showBrush?: boolean;
  strokeWidth?: number;
  interactiveZoom?: boolean;
  hideAxes?: boolean;
  hideTooltip?: boolean;
  unit?: string;
}

const LineChart = React.memo(({
  data,
  dataKey = 'value',
  xAxisKey = 'name',
  color = '#3b82f6',
  height = '100%',
  referenceRange,
  showReferenceLines = false,
  chartType = 'line',
  showGrid = true,
  showBrush = false,
  strokeWidth = 3,
  interactiveZoom = false,
  hideAxes = false,
  hideTooltip = false,
  unit = '',
}: LineChartProps) => {
  const theme = useSettingsStore(state => state.theme);
  const isDark = theme === 'dark';

  const chartRef = React.useRef<HTMLDivElement>(null);
  const [zoomIndices, setZoomIndices] = useState<{start: number, end: number} | null>(null);
  const [isPanning, setIsPanning] = useState(false);
  const panInfo = React.useRef({ startX: 0 });

  useEffect(() => {
    setZoomIndices(null);
  }, [data.length, dataKey]);

  const displayData = useMemo(() => {
    const formatted = data.map((item) => ({
      name: item.name,
      [dataKey]: item.value,
    }));
    
    if (zoomIndices) {
      return formatted.slice(zoomIndices.start, zoomIndices.end + 1);
    }
    return formatted;
  }, [data, dataKey, zoomIndices]);

  const handleWheel = useCallback((e: WheelEvent) => {
    if (!interactiveZoom || data.length < 2) return;
    
    e.preventDefault();
    e.stopPropagation();

    const delta = e.deltaY;
    setZoomIndices(prev => {
      const currentStart = prev?.start ?? 0;
      const currentEnd = prev?.end ?? data.length - 1;
      const range = currentEnd - currentStart;
      
      const zoomFactor = Math.max(1, Math.floor(data.length * 0.05));
      
      let newStart = currentStart;
      let newEnd = currentEnd;

      if (delta < 0) {
        if (range > 1) {
          newStart = Math.min(currentEnd - 1, currentStart + zoomFactor);
          newEnd = Math.max(currentStart + 1, currentEnd - zoomFactor);
        }
      } else {
        newStart = Math.max(0, currentStart - zoomFactor);
        newEnd = Math.min(data.length - 1, currentEnd + zoomFactor);
      }

      if (newStart <= 0 && newEnd >= data.length - 1) return null;
      return { start: Math.max(0, newStart), end: Math.min(data.length - 1, newEnd) };
    });
  }, [interactiveZoom, data.length]);

  useEffect(() => {
    const el = chartRef.current;
    if (el) {
      const blocker = (e: WheelEvent) => {
        if (interactiveZoom) {
          e.preventDefault();
          e.stopPropagation();
          handleWheel(e);
        }
      };
      el.addEventListener('wheel', blocker, { passive: false });
      return () => el.removeEventListener('wheel', blocker);
    }
  }, [interactiveZoom, handleWheel]);

  const handleMouseDown = (e: any) => {
    if (!interactiveZoom || !zoomIndices) return;
    setIsPanning(true);
    panInfo.current.startX = e.chartX;
  };

  const handleMouseMove = (e: any) => {
    if (!isPanning || !interactiveZoom || !zoomIndices || !e) return;
    
    const dx = e.chartX - panInfo.current.startX;
    if (Math.abs(dx) < 5) return; 

    const range = zoomIndices.end - zoomIndices.start;
    const shift = dx > 0 ? -1 : 1; 
    
    setZoomIndices(prev => {
      if (!prev) return null;
      const newStart = Math.max(0, Math.min(data.length - 1 - range, prev.start + shift));
      return { start: newStart, end: newStart + range };
    });
    
    panInfo.current.startX = e.chartX;
  };

  const handleMouseUp = () => {
    setIsPanning(false);
  };

  const yDomain: [number | string, number | string] = useMemo(() => {
    if (!data || data.length === 0) return [0, 'auto'];
    
    const values = displayData.map(d => d[dataKey] as number);
    if (referenceRange) {
      if (referenceRange.min != null) values.push(referenceRange.min);
      if (referenceRange.max != null) values.push(referenceRange.max);
    }
    
    const minVal = Math.min(...values);
    const maxVal = Math.max(...values);
    
    const range = maxVal - minVal;
    // Add 15% padding to top and bottom
    const padding = range === 0 ? Math.abs(maxVal) * 0.1 || 1 : range * 0.05;
    
    let domainMin = minVal - padding;
    let domainMax = maxVal + padding;

    // For medical data, if the values are all positive and close to 0, 
    // keep 0 as a baseline. Otherwise, let it be dynamic.
    if (minVal >= 0 && domainMin < minVal * 0.2) {
      domainMin = 0;
    }

    return [domainMin, domainMax];
  }, [displayData, dataKey, referenceRange, data]);

  const renderChartContent = () => {
    switch (chartType) {
      case 'area':
        return (
          <>
            <defs>
              <linearGradient id={`colorValue-${dataKey}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={color} stopOpacity={0.3}/>
                <stop offset="95%" stopColor={color} stopOpacity={0}/>
              </linearGradient>
            </defs>
            <Area
              type="monotone"
              dataKey={dataKey}
              stroke={color}
              strokeWidth={strokeWidth}
              fillOpacity={1}
              fill={`url(#colorValue-${dataKey})`}
              dot={{ r: 3, strokeWidth: 2, fill: isDark ? '#1e293b' : '#fff' }}
              activeDot={{ r: 6, strokeWidth: 0 }}
              isAnimationActive={!isPanning}
            />
          </>
        );
      case 'bar':
        return (
          <Bar 
            dataKey={dataKey} 
            fill={color} 
            radius={[4, 4, 0, 0]}
            maxBarSize={40}
            isAnimationActive={!isPanning}
          />
        );
      default:
        return (
          <Line
            type="monotone"
            dataKey={dataKey}
            stroke={color}
            strokeWidth={strokeWidth}
            dot={{ r: 4, strokeWidth: 2, fill: isDark ? '#1e293b' : '#fff' }}
            activeDot={{ r: 6, strokeWidth: 0 }}
            isAnimationActive={!isPanning}
          />
        );
    }
  };

  const ChartComponent = chartType === 'area' ? AreaChart : chartType === 'bar' ? BarChart : RechartsLineChart;

  return (
    <div className="relative w-full h-full group/chart nodrag" ref={chartRef}>
      {zoomIndices && (
        <div className="absolute top-0 right-10 z-50 flex items-center space-x-2">
           <button 
            onClick={() => setZoomIndices(null)}
            className="p-1.5 bg-white/80 dark:bg-dark-surface/80 backdrop-blur-sm border border-gray-100 dark:border-dark-border rounded-lg shadow-sm text-gray-400 hover:text-blue-500 transition-all flex items-center space-x-1"
            title="Reset Zoom"
          >
            <RefreshCw className="w-3 h-3" />
            <span className="text-[9px] font-black uppercase">Reset</span>
          </button>
        </div>
      )}

      {interactiveZoom && !zoomIndices && (
        <div className="absolute bottom-10 left-1/2 -translate-x-1/2 z-10 opacity-0 group-hover/chart:opacity-100 transition-opacity pointer-events-none w-full text-center px-4">
          <div className="inline-flex px-3 py-1.5 bg-black/60 backdrop-blur-md rounded-full text-[9px] font-black text-white uppercase tracking-widest items-center space-x-2">
            <ZoomIn className="w-3 h-3" />
            <span>Scroll to Zoom • Drag to Pan</span>
          </div>
        </div>
      )}

      <ResponsiveContainer width="100%" height={height}>
        {/* @ts-ignore */}
        <ChartComponent 
          data={displayData} 
          margin={{ top: 5, right: 5, left: 0, bottom: 0 }}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          style={{ cursor: isPanning ? 'grabbing' : interactiveZoom ? 'crosshair' : 'default' }}
        >
          {showGrid && (
            <CartesianGrid 
              strokeDasharray="3 3" 
              stroke={isDark ? '#334155' : '#e5e7eb'} 
              vertical={false} 
            />
          )}
          {!hideAxes && (
            <XAxis 
              dataKey={xAxisKey} 
              stroke={isDark ? '#94a3b8' : '#9ca3af'} 
              fontSize={10} 
              tickLine={false} 
              axisLine={false}
              dy={0}
              padding={{ left: 10, right: 10 }}
            />
          )}
          {!hideAxes && (
            <YAxis 
              stroke={isDark ? '#94a3b8' : '#9ca3af'} 
              fontSize={10} 
              tickLine={false} 
              axisLine={false}
              width={38}
              tickFormatter={(value) => {
                if (value === undefined || value === null) return '';
                const valStr = value.toString();
                return valStr.length > 5 ? `${parseFloat(value.toPrecision(3))}` : value;
              }}
              domain={yDomain}
              allowDecimals={true}
            />
          )}
          {!hideTooltip && (
            <Tooltip
              content={hideAxes ? ({ active, payload }: any) => {
                if (active && payload && payload.length) {
                  return (
                    <div className="bg-black/80 backdrop-blur-sm px-2 py-1.5 rounded-lg border border-white/10 shadow-xl flex flex-col items-center">
                      <div className="flex items-baseline space-x-1">
                        <p className="text-[10px] font-black text-white">{payload[0].value}</p>
                        {unit && <p className="text-[8px] font-bold text-gray-400 uppercase">{unit}</p>}
                      </div>
                      {payload[0].payload.name && (
                        <p className="text-[8px] font-medium text-gray-400 mt-0.5">{payload[0].payload.name}</p>
                      )}
                    </div>
                  );
                }
                return null;
              } : undefined}
              contentStyle={!hideAxes ? {
                backgroundColor: isDark ? '#1e293b' : '#fff',
                border: isDark ? '1px solid #334155' : 'none',
                borderRadius: '0.75rem',
                boxShadow: '0 10px 15px -3px rgba(0, 0, 0, 0.1)',
                color: isDark ? '#f8fafc' : '#000',
                zIndex: 100,
              } : undefined}
              itemStyle={!hideAxes ? {
                color: isDark ? '#f8fafc' : '#000',
              } : undefined}
            />
          )}
          {showReferenceLines && referenceRange && (
            <>
              {(referenceRange.min != null || referenceRange.max != null) && (
                <ReferenceArea 
                  y1={referenceRange.min ?? 0} 
                  y2={referenceRange.max ?? undefined} 
                  fill={isDark ? "rgba(34, 197, 94, 0.15)" : "rgba(34, 197, 94, 0.08)"} 
                  stroke="none"
                />
              )}
              
              {referenceRange.min !== undefined && referenceRange.min !== null && (
                <ReferenceLine 
                  y={referenceRange.min} 
                  stroke={isDark ? "rgba(34, 197, 94, 0.4)" : "rgba(34, 197, 94, 0.3)"} 
                  strokeDasharray="3 3"
                  strokeWidth={1.5}
                >
                  <Label value="Min" position="insideLeft" fill={isDark ? "#4ade80" : "#22c55e"} fontSize={8} fontWeight="bold" />
                </ReferenceLine>
              )}
              {referenceRange.max !== undefined && referenceRange.max !== null && (
                <ReferenceLine 
                  y={referenceRange.max} 
                  stroke={isDark ? "rgba(34, 197, 94, 0.4)" : "rgba(34, 197, 94, 0.3)"} 
                  strokeDasharray="3 3"
                  strokeWidth={1.5}
                >
                  <Label value="Max" position="insideLeft" fill={isDark ? "#4ade80" : "#22c55e"} fontSize={8} fontWeight="bold" />
                </ReferenceLine>
              )}
            </>
          )}
          {renderChartContent()}
          {showBrush && !zoomIndices && data.length > 5 && (
            <Brush 
              dataKey={xAxisKey} 
              height={20} 
              stroke={color} 
              fill={isDark ? '#0f172a' : '#f8fafc'}
              travellerWidth={10}
            />
          )}
        </ChartComponent>
      </ResponsiveContainer>
    </div>
  );
});

export default LineChart;