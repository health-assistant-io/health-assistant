import React from 'react';
import { useTranslation } from 'react-i18next';

interface Alert {
  type: string;
  message: string;
  timestamp: string;
}

interface AlertsProps {
  alerts: Alert[];
}

const Alerts: React.FC<AlertsProps> = ({ alerts }) => {
  const { t } = useTranslation();

  if (alerts.length === 0) {
    return (
      <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-6 h-full flex flex-col">
        <h2 className="text-lg font-bold text-gray-900 dark:text-dark-text tracking-tight mb-4">
          {t('alerts_page.dashboard_card.title')}
        </h2>
        <div className="flex-1 flex flex-col items-center justify-center text-center py-10 opacity-40">
           <svg className="w-12 h-12 mb-2 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
           <p className="text-sm font-medium">{t('alerts_page.dashboard_card.empty')}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-6 h-full flex flex-col">
      <h2 className="text-lg font-bold text-gray-900 dark:text-dark-text tracking-tight mb-6">
        {t('alerts_page.dashboard_card.title')}
      </h2>
      <div className="space-y-3 overflow-y-auto flex-1 custom-scrollbar">
        {alerts.map((alert, index) => (
          <div
            key={index}
            className={`p-4 rounded-xl border transition-all ${
              alert.type === 'critical' 
                ? 'bg-red-50 dark:bg-red-900/10 border-red-100 dark:border-red-900/30 text-red-700 dark:text-red-400' 
                : 'bg-yellow-50 dark:bg-yellow-900/10 border-yellow-100 dark:border-yellow-900/30 text-yellow-700 dark:text-yellow-400'
            }`}
          >
            <div className="flex items-start space-x-3">
                <div className={`mt-0.5 shrink-0 w-2 h-2 rounded-full ${alert.type === 'critical' ? 'bg-red-500 animate-pulse' : 'bg-yellow-500'}`}></div>
                <div>
                    <p className="text-sm font-bold leading-tight">{alert.message}</p>
                    <p className="text-[10px] font-medium opacity-60 mt-1 uppercase tracking-wider">
                        {new Date(alert.timestamp).toLocaleString()}
                    </p>
                </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default Alerts;