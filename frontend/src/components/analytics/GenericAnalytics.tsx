import React from 'react';
import LineChart from '../charts/LineChart';

interface GenericAnalyticsProps {
  data: any;
  loading: boolean;
  title: string;
}

const GenericAnalytics: React.FC<GenericAnalyticsProps> = ({ data, loading, title }) => {
  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  const reports = data?.reports || [];
  const trends = data?.biomarkers || {};
  const hasReports = reports.length > 0;
  const hasBiomarkers = Object.keys(trends).length > 0;

  if (!hasReports && !hasBiomarkers) {
    return (
      <div className="bg-white dark:bg-dark-surface rounded-lg shadow p-6 text-center">
        <p className="text-gray-500 dark:text-dark-muted">
          No matching data found for {title}.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Extracted Biomarkers Section */}
      {hasBiomarkers && (
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
              </div>
            );
          })}
        </div>
      )}

      {/* Reports/Documents List Section */}
      {hasReports && (
        <div className="bg-white dark:bg-dark-surface rounded-lg shadow p-6">
          <h2 className="text-xl font-semibold text-gray-900 dark:text-dark-text mb-4">{title} Documents</h2>
          <div className="mt-4 overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
              <thead className="bg-gray-50 dark:bg-dark-border">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-dark-muted uppercase tracking-wider">Date</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-dark-muted uppercase tracking-wider">Document Name</th>
                </tr>
              </thead>
              <tbody className="bg-white dark:bg-dark-surface divide-y divide-gray-200 dark:divide-gray-700">
                {reports.map((report: any, idx: number) => (
                  <tr key={idx} className="hover:bg-gray-50 dark:hover:bg-dark-border transition-colors">
                    <td className="px-4 py-4 whitespace-nowrap text-sm text-gray-900 dark:text-dark-text">
                      {new Date(report.date).toLocaleDateString()}
                    </td>
                    <td className="px-4 py-4 whitespace-nowrap text-sm text-blue-600 dark:text-blue-400">
                      {report.document_name}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};

export default GenericAnalytics;