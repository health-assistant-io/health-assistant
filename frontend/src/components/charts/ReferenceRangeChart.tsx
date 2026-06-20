import { useMemo } from 'react';
import { LineChart as RechartsLineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { useBiomarkerPrecisionProfile } from '../../hooks/useBiomarkerPrecision';
import { formatBiomarkerValue } from '../../utils/biomarkerUtils';

interface ReferenceRangeChartProps {
  data: Array<{ name: string; value: number }>;
  referenceRange?: {
    min: number;
    max: number;
  };
  dataKey: string;
  xAxisKey: string;
  height?: number;
}

const ReferenceRangeChart: React.FC<ReferenceRangeChartProps> = ({
  data,
  referenceRange,
  dataKey = 'value',
  xAxisKey = 'name',
  height = 300,
}) => {
  const precisionProfile = useBiomarkerPrecisionProfile();

  const formattedData = useMemo(() => {
    return data.map((item) => ({
      name: item.name,
      [dataKey]: item.value,
    }));
  }, [data, dataKey]);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <RechartsLineChart data={formattedData} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
        <XAxis dataKey={xAxisKey} stroke="#6b7280" />
        <YAxis stroke="#6b7280" width={45} />
        <Tooltip
          contentStyle={{
            backgroundColor: '#fff',
            border: '1px solid #e5e7eb',
            borderRadius: '0.5rem',
          }}
          formatter={(value: any) => [formatBiomarkerValue(value, precisionProfile), dataKey]}
        />
        <Line
          type="monotone"
          dataKey={dataKey}
          stroke="#3b82f6"
          strokeWidth={2}
          dot={{ r: 4 }}
          activeDot={{ r: 6 }}
        />
        {referenceRange && (
          <>
            <Line
              type="monotone"
              dataKey="_min"
              stroke="#f59e0b"
              strokeDasharray="5 5"
              strokeWidth={2}
              name="Minimum reference"
            />
            <Line
              type="monotone"
              dataKey="_max"
              stroke="#f59e0b"
              strokeDasharray="5 5"
              strokeWidth={2}
              name="Maximum reference"
            />
          </>
        )}
      </RechartsLineChart>
    </ResponsiveContainer>
  );
};

export default ReferenceRangeChart;
