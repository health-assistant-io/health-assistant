interface VitalStatsProps {
  stats: {
    totalDocuments: number;
    totalObservations: number;
    activeAlerts: number;
    patients: number;
  };
}

const VitalStats: React.FC<VitalStatsProps> = ({ stats }) => {
  return (
    <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
      <div className="bg-white dark:bg-dark-surface rounded-lg shadow p-6">
        <h3 className="text-sm font-medium text-gray-500 dark:text-dark-muted">Total Documents</h3>
        <p className="text-3xl font-bold text-gray-900 dark:text-dark-text mt-2">
          {stats.totalDocuments}
        </p>
      </div>
      <div className="bg-white dark:bg-dark-surface rounded-lg shadow p-6">
        <h3 className="text-sm font-medium text-gray-500 dark:text-dark-muted">Total Observations</h3>
        <p className="text-3xl font-bold text-gray-900 dark:text-dark-text mt-2">
          {stats.totalObservations}
        </p>
      </div>
      <div className="bg-white dark:bg-dark-surface rounded-lg shadow p-6">
        <h3 className="text-sm font-medium text-gray-500 dark:text-dark-muted">Active Alerts</h3>
        <p className="text-3xl font-bold text-red-600 mt-2">
          {stats.activeAlerts}
        </p>
      </div>
      <div className="bg-white dark:bg-dark-surface rounded-lg shadow p-6">
        <h3 className="text-sm font-medium text-gray-500 dark:text-dark-muted">Patients</h3>
        <p className="text-3xl font-bold text-gray-900 dark:text-dark-text mt-2">
          {stats.patients}
        </p>
      </div>
    </div>
  );
};

export default VitalStats;