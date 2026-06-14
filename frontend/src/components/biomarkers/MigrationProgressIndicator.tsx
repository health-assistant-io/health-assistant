import React from 'react';
import { Activity, AlertCircle, Clock, RefreshCw } from 'lucide-react';

interface MigrationProgressIndicatorProps {
  status?: 'in_progress' | 'completed' | 'failed';
  progress?: number;
  errorMessage?: string;
  onRetry?: () => void;
}

export function MigrationProgressIndicator({
  status,
  progress = 0,
  errorMessage,
  onRetry
}: MigrationProgressIndicatorProps) {
  if (!status || status === 'completed') return null;

  const isFailed = status === 'failed';

  return (
    <div className={`p-4 border rounded-xl mb-6 ${isFailed ? 'bg-red-50/50 dark:bg-red-900/10 border-red-100 dark:border-red-900/20' : 'bg-blue-50/50 dark:bg-blue-900/10 border-blue-100 dark:border-blue-900/20'}`}>
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center space-x-3">
          <div className={`p-2 rounded-lg ${isFailed ? 'bg-red-100 dark:bg-red-900/30' : 'bg-blue-100 dark:bg-blue-900/30 animate-pulse'}`}>
            {isFailed ? <AlertCircle className="w-4 h-4 text-red-600 dark:text-red-400" /> : <Activity className="w-4 h-4 text-blue-600 dark:text-blue-400" />}
          </div>
          <div>
            <h4 className={`text-xs font-bold uppercase tracking-widest ${isFailed ? 'text-red-900 dark:text-dark-text' : 'text-blue-900 dark:text-dark-text'}`}>
              {isFailed ? 'Migration Failed' : 'Data Migration in Progress'}
            </h4>
            <p className={`text-[10px] font-bold uppercase ${isFailed ? 'text-red-600/70 dark:text-red-400/70' : 'text-blue-600/70 dark:text-blue-400/70'}`}>
              {isFailed ? 'An error occurred during migration' : 'Re-indexing historical records'}
            </p>
          </div>
        </div>
        <div className="flex items-center space-x-2">
          {!isFailed && <Clock className="w-4 h-4 text-blue-600/50 animate-spin-slow" />}
        </div>
      </div>

      <div className="mb-2">
        <div className="flex items-center justify-between mb-1">
          <span className={`text-[10px] font-bold uppercase tracking-widest ${isFailed ? 'text-red-600' : 'text-blue-600'}`}>
            {isFailed ? 'Failed' : 'Processing'}
          </span>
          {!isFailed && (
            <span className="text-[10px] font-bold text-blue-600 uppercase">
              {progress}%
            </span>
          )}
        </div>
        {!isFailed && (
          <div className="h-1.5 bg-blue-200/50 dark:bg-blue-900/20 rounded-full overflow-hidden">
            <div 
              className="h-full bg-blue-600 transition-all duration-1000 ease-out"
              style={{ width: `${progress}%` }}
            />
          </div>
        )}
      </div>
      
      {isFailed && errorMessage && (
        <div className="mt-3 p-2.5 bg-red-100/50 dark:bg-red-900/30 text-red-700 dark:text-red-400 text-xs rounded border border-red-200/50 dark:border-red-800 break-words font-mono">
          {errorMessage}
        </div>
      )}
      
      {onRetry && (
        <div className="mt-3 pt-3 border-t border-black/5 dark:border-white/5 flex justify-end">
          <button 
            onClick={onRetry}
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-all active:scale-95 ${
              isFailed 
                ? 'bg-red-600 text-white hover:bg-red-700 shadow-sm shadow-red-200 dark:shadow-none' 
                : 'bg-white/50 dark:bg-dark-bg text-blue-600 hover:bg-white dark:hover:bg-dark-surface border border-blue-100 dark:border-blue-900/30'
            }`}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${!isFailed && 'text-blue-500'}`} />
            <span>{isFailed ? 'Retry Migration' : 'Force Restart'}</span>
          </button>
        </div>
      )}
    </div>
  );
}
