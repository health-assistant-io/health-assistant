import { useMemo } from 'react';
import { BarChart as RechartsBarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

interface BarChartProps {
  data: Array<{ name: string; value: number }>;
  dataKey: string;
  xAxisKey: string;
  color?: string;
  height?: number;
}

const BarChart: React.FC<BarChartProps> = ({
  data,
  dataKey = 'value',
  xAxisKey = 'name',
  color = '#3b82f6',
  height = 300,
}) => {
  const formattedData = useMemo(() => {
    return data.map((item) => ({
      name: item.name,
      [dataKey]: item.value,
    }));
  }, [data, dataKey]);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <RechartsBarChart data={formattedData}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
        <XAxis dataKey={xAxisKey} stroke="#6b7280" />
        <YAxis stroke="#6b7280" />
        <Tooltip
          contentStyle={{
            backgroundColor: '#fff',
            border: '1px solid #e5e7eb',
            borderRadius: '0.5rem',
          }}
        />
        <Legend />
        <Bar dataKey={dataKey} fill={color} radius={[4, 4, 0, 0]} />
      </RechartsBarChart>
    </ResponsiveContainer>
  );
};

export default BarChart;