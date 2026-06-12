import React from 'react';
import LineChart from '../charts/LineChart';

interface LabResultsAnalyticsProps {
  data: any;
  loading: boolean;
}

const LabResultsAnalytics: React.FC<LabResultsAnalyticsProps> = ({ data, loading }) => {
  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  const trends = data?.biomarkers || {};
  const hasData = Object.keys(trends).length > 0;

  if (!hasData) {
    return (
      <div className="bg-white dark:bg-dark-surface rounded-lg shadow p-6 text-center">
        <p className="text-gray-500 dark:text-dark-muted">
          No matching laboratory test data found.
        </p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      {Object.entries(trends).map(([key, biomarkerData]: [string, any]) => {
        if (!biomarkerData || biomarkerData.length === 0) return null;
        
        const displayName = biomarkerData[0]?.name || key;
        const unit = biomarkerData[0]?.unit || '';
        const latest = biomarkerData[biomarkerData.length - 1];
        
        return (
          <div key={key} className="bg-white dark:bg-dark-surface rounded-lg shadow p-6">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-xl font-semibold text-gray-900 dark:text-dark-text capitalize">
                  {displayName}
                </h2>
                <p className="text-sm text-gray-500 dark:text-dark-muted">
                  Latest: {latest.value} {unit} ({new Date(latest.date).toLocaleDateString()})
                </p>
              </div>
            </div>
            
            <LineChart 
              data={biomarkerData.map((d: any) => ({
                name: new Date(d.date).toLocaleDateString(),
                value: d.value
              }))}
              dataKey="value"
              xAxisKey="name"
              height={250}
            />
            
            <div className="mt-4 overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                <thead className="bg-gray-50 dark:bg-dark-border">
                  <tr>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-dark-muted uppercase">Date</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-dark-muted uppercase">Value</th>
                  </tr>
                </thead>
                <tbody className="bg-white dark:bg-dark-surface divide-y divide-gray-200 dark:divide-gray-700">
                  {[...biomarkerData].reverse().slice(0, 5).map((d: any, idx: number) => (
                    <tr key={idx}>
                      <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-900 dark:text-dark-text">
                        {new Date(d.date).toLocaleDateString()}
                      </td>
                      <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-500 dark:text-dark-muted">
                        {d.value} {d.unit}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default LabResultsAnalytics;
