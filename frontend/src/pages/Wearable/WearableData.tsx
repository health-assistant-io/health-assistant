import { useState } from 'react';
import { uploadWearableData, getWearableData, getWearableSummary } from '../../services/wearableService';
import { WearableDataItem, WearableSummary } from '../../types/wearable';
import { PageHeader } from '../../components/ui/PageHeader';
import { Activity } from 'lucide-react';



function Wearable() {
  const [deviceId, setDeviceId] = useState('');
  const [data, setData] = useState<WearableDataItem[]>([]);
  const [summary, setSummary] = useState<WearableSummary | null>(null);

  const handleUpload = async () => {
    if (!deviceId) return;

    const sampleData = [
      {
        timestamp: new Date().toISOString(),
        heart_rate: 72,
        steps: 1500,
        calories: 250,
      },
    ];

    try {
      await uploadWearableData(deviceId, sampleData);
      alert('Data uploaded successfully!');
    } catch (error) {
      console.error('Upload failed:', error);
    }
  };

  const handleFetchData = async () => {
    if (!deviceId) return;

    try {
      const startDate = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString();
      const endDate = new Date().toISOString();
      const fetchData = await getWearableData(deviceId, startDate, endDate);
      setData(fetchData);

      const summaryData = await getWearableSummary(new Date().toISOString(), deviceId) as WearableSummary;
      setSummary(summaryData);
    } catch (error) {
      console.error('Failed to fetch data:', error);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Wearable Data"
        subtitle="Monitor your real-time health metrics from connected devices"
        icon={<Activity className="w-8 h-8" />}
      />

      <div className="bg-white dark:bg-dark-surface rounded-lg shadow p-6">
        <h2 className="text-xl font-semibold text-gray-900 dark:text-dark-text mb-4">
          Connect Wearable Device
        </h2>

        <div className="flex space-x-4">
          <input
            type="text"
            value={deviceId}
            onChange={(e) => setDeviceId(e.target.value)}
            className="flex-1 px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg focus:ring-2 focus:ring-blue-500 dark:bg-dark-border dark:text-dark-text"
            placeholder="Enter device ID"
          />
          <button
            onClick={handleUpload}
            className="px-6 py-2.5 bg-blue-600 text-white rounded-xl font-bold hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none active:scale-95"
          >
            Upload Sample
          </button>
          <button
            onClick={handleFetchData}
            className="px-6 py-2.5 border border-gray-200 dark:border-dark-border text-gray-700 dark:text-dark-text rounded-xl font-bold hover:bg-gray-50 dark:hover:bg-dark-surface transition-all active:scale-95"
          >
            Fetch Data
          </button>
        </div>
      </div>

      {summary && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div className="bg-white dark:bg-dark-surface rounded-lg shadow p-6">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-dark-text mb-2">
              Steps
            </h3>
            <p className="text-3xl font-bold text-gray-900 dark:text-dark-text">{summary.steps}</p>
            <p className="text-sm text-gray-500 dark:text-dark-muted">Today</p>
          </div>
          <div className="bg-white dark:bg-dark-surface rounded-lg shadow p-6">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-dark-text mb-2">
              Calories
            </h3>
            <p className="text-3xl font-bold text-gray-900 dark:text-dark-text">{summary.calories}</p>
            <p className="text-sm text-gray-500 dark:text-dark-muted">Today</p>
          </div>
          <div className="bg-white dark:bg-dark-surface rounded-lg shadow p-6">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-dark-text mb-2">
              Heart Rate
            </h3>
            <div className="flex items-center space-x-2">
              <p className="text-3xl font-bold text-gray-900 dark:text-dark-text">
                {summary.heart_rate.avg}
              </p>
              <span className="text-sm text-gray-500 dark:text-dark-muted">
                avg ({summary.heart_rate.min}-{summary.heart_rate.max})
              </span>
            </div>
            <p className="text-sm text-gray-500 dark:text-dark-muted">Today</p>
          </div>
        </div>
      )}

      {data.length > 0 && (
        <div className="bg-white dark:bg-dark-surface rounded-lg shadow p-6">
          <h2 className="text-xl font-semibold text-gray-900 dark:text-dark-text mb-4">
            Recent Data
          </h2>
          <div className="space-y-2">
            {data.slice(0, 5).map((item, index) => (
              <div
                key={item.id || index}
                className="flex items-center justify-between p-3 border border-gray-200 dark:border-dark-border rounded-lg"
              >
                <span className="text-sm text-gray-500 dark:text-dark-muted">
                  {new Date(item.timestamp).toLocaleTimeString()}
                </span>
                <div className="flex space-x-4">
                  {item.heart_rate && (
                    <span className="text-sm text-gray-900 dark:text-dark-text">
                      HR: {item.heart_rate} bpm
                    </span>
                  )}
                  {item.steps && (
                    <span className="text-sm text-gray-900 dark:text-dark-text">
                      Steps: {item.steps}
                    </span>
                  )}
                  {item.calories && (
                    <span className="text-sm text-gray-900 dark:text-dark-text">
                      Cal: {item.calories}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default Wearable;