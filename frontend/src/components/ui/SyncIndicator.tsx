import { useEffect, useState } from 'react';
import { db } from '../../services/db';
import { Cloud, CloudOff, RefreshCw } from 'lucide-react';
import { useLiveQuery } from 'dexie-react-hooks';

export function SyncIndicator({ className = '' }: { className?: string }) {
  const [isOnline, setIsOnline] = useState(navigator.onLine);
  
  // Use Dexie live query to observe the pending queue size
  const pendingCount = useLiveQuery(() => db.pendingSync.where('status').equals('pending').count()) || 0;
  const syncingCount = useLiveQuery(() => db.pendingSync.where('status').equals('syncing').count()) || 0;

  useEffect(() => {
    const handleStatusChange = () => setIsOnline(navigator.onLine);
    window.addEventListener('online', handleStatusChange);
    window.addEventListener('offline', handleStatusChange);
    return () => {
      window.removeEventListener('online', handleStatusChange);
      window.removeEventListener('offline', handleStatusChange);
    };
  }, []);

  if (!isOnline) {
    return (
      <div className={`flex items-center justify-center gap-2 px-3 py-1.5 bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400 rounded-full text-xs font-medium border border-amber-200 dark:border-amber-800 ${className}`}>
        <CloudOff className="w-3.5 h-3.5 flex-shrink-0" />
        <span className="truncate">Offline (Drafting)</span>
        {pendingCount > 0 && (
          <span className="ml-1 px-1.5 py-0.5 bg-amber-200 dark:bg-amber-800 rounded-full text-[10px]">
            {pendingCount}
          </span>
        )}
      </div>
    );
  }

  if (syncingCount > 0) {
    return (
      <div className={`flex items-center justify-center gap-2 px-3 py-1.5 bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400 rounded-full text-xs font-medium border border-blue-200 dark:border-blue-800 ${className}`}>
        <RefreshCw className="w-3.5 h-3.5 animate-spin flex-shrink-0" />
        <span className="truncate">Syncing...</span>
      </div>
    );
  }

  if (pendingCount > 0) {
    return (
      <div className={`flex items-center justify-center gap-2 px-3 py-1.5 bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400 rounded-full text-xs font-medium border border-amber-200 dark:border-amber-800 ${className}`}>
        <Cloud className="w-3.5 h-3.5 flex-shrink-0" />
        <span className="truncate">Pending Sync ({pendingCount})</span>
      </div>
    );
  }

  return (
    <div className={`flex items-center justify-center gap-2 px-3 py-1.5 bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400 rounded-full text-xs font-medium border border-emerald-200 dark:border-emerald-800 ${className}`}>
      <Cloud className="w-3.5 h-3.5 flex-shrink-0" />
      <span className="truncate">Synced</span>
    </div>
  );
}
