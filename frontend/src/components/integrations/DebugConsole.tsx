import React, { useState, useEffect } from 'react';
import { Terminal, RefreshCw, AlertCircle } from 'lucide-react';
import { integrationService } from '../../services/integrationService';
import { toast } from 'react-toastify';

interface DebugConsoleProps {
  integrationId: string;
  patientId: string;
}

export const DebugConsole: React.FC<DebugConsoleProps> = ({ integrationId, patientId }) => {
  const [logs, setLogs] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  const fetchLogs = async () => {
    setLoading(true);
    try {
      const data = await integrationService.getDebugLogs(integrationId, patientId);
      setLogs(data);
    } catch (error) {
      console.error("Failed to fetch debug logs", error);
      toast.error("Failed to refresh debug logs");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchLogs();
  }, [integrationId, patientId]);

  return (
    <div className="bg-gray-900 rounded-[2rem] p-6 shadow-sm overflow-hidden flex flex-col">
      <div className="flex items-center justify-between mb-4 pb-4 border-b border-gray-800">
        <h3 className="flex items-center text-sm font-bold text-gray-100 uppercase tracking-widest">
          <Terminal className="w-4 h-4 mr-2 text-green-400" /> Debug Console
        </h3>
        <button
          onClick={fetchLogs}
          disabled={loading}
          className="flex items-center px-3 py-1.5 bg-gray-800 text-gray-300 rounded-lg text-xs hover:bg-gray-700 transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-3 h-3 mr-2 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      <div className="bg-black/50 rounded-xl p-4 flex-1 max-h-96 overflow-y-auto font-mono text-xs text-green-400 space-y-4">
        {logs.length === 0 ? (
          <p className="text-gray-500 italic">No debug logs available. Ensure "Enable Debug Mode" is checked in the configuration.</p>
        ) : (
          logs.map((log) => (
            <div key={log.id} className="border-l-2 border-gray-700 pl-3">
              <div className="flex items-center justify-between text-gray-500 mb-1">
                <span>{new Date(log.timestamp).toLocaleString()}</span>
                <span className={`uppercase font-bold ${log.level === 'error' ? 'text-red-400' : 'text-blue-400'}`}>
                  {log.level}
                </span>
              </div>
              <div className="text-gray-300 mb-1 font-bold">{log.title}</div>
              <pre className="overflow-x-auto bg-black p-2 rounded whitespace-pre-wrap break-all text-[10px]">
                {log.payload ? JSON.stringify(log.payload, null, 2) : "Empty Payload"}
              </pre>
            </div>
          ))
        )}
      </div>
      
      <div className="mt-4 flex items-start text-xs text-yellow-500/80 bg-yellow-500/10 p-3 rounded-lg">
        <AlertCircle className="w-4 h-4 mr-2 shrink-0 mt-0.5" />
        <p>Debug logs consume database space and may contain sensitive raw data. Remember to disable Debug Mode when you are finished testing.</p>
      </div>
    </div>
  );
};
