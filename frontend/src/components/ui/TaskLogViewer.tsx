import { useState, useEffect } from 'react';
import { getExaminationLogs } from '../../services/examinationService';
import { X, Terminal, Clock, AlertCircle, CheckCircle, Activity, Copy, Check, ChevronDown, ChevronRight } from 'lucide-react';
import { useModalA11y } from '../../hooks/useModalA11y';

interface TaskLog {
  id: string;
  task_name: string;
  level: string;
  stage?: string;
  message: string;
  data?: any;
  created_at: string;
}

interface TaskLogViewerProps {
  examinationId: string;
  onClose: () => void;
  isOpen: boolean;
}

export function TaskLogViewer({ examinationId, onClose, isOpen }: TaskLogViewerProps) {
  const [logs, setLogs] = useState<TaskLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedLogs, setExpandedLogs] = useState<Set<string>>(new Set());
  const [copiedId, setCopiedId] = useState<string | null>(null);

  useModalA11y(isOpen, onClose);

  useEffect(() => {
    if (isOpen) {
      fetchLogs();
      const interval = setInterval(fetchLogs, 3000);
      return () => clearInterval(interval);
    }
  }, [isOpen, examinationId]);

  const fetchLogs = async () => {
    try {
      const data = await getExaminationLogs(examinationId);
      setLogs(data);
    } catch (err) {
      console.error("Failed to fetch logs", err);
    } finally {
      setLoading(false);
    }
  };

  const toggleLog = (id: string) => {
    setExpandedLogs(prev => {
      const newSet = new Set(prev);
      if (newSet.has(id)) {
        newSet.delete(id);
      } else {
        newSet.add(id);
      }
      return newSet;
    });
  };

  const copyToClipboard = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[1000] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-in fade-in duration-200">
      <div role="dialog" aria-modal="true" className="bg-[#0f172a] border border-slate-800 w-full max-w-6xl max-h-[90vh] rounded-3xl shadow-2xl flex flex-col overflow-hidden animate-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between bg-slate-900/50">
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-blue-500/10 rounded-xl">
              <Terminal className="w-5 h-5 text-blue-400" />
            </div>
            <div>
              <h3 className="text-sm font-black text-slate-100 uppercase tracking-widest">Pipeline Task Monitor</h3>
              <p className="text-[10px] font-bold text-slate-500 uppercase">Technical execution logs for AI processors</p>
            </div>
          </div>
          <button 
            onClick={onClose}
            className="p-2 hover:bg-slate-800 rounded-xl text-slate-400 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto overflow-x-hidden p-6 font-mono text-xs space-y-2 no-scrollbar">
          {loading && logs.length === 0 ? (
            <div className="flex items-center justify-center py-20 text-slate-500">
              <Activity className="w-5 h-5 animate-spin mr-2" />
              <span>Attaching to pipeline output...</span>
            </div>
          ) : logs.length === 0 ? (
            <div className="text-center py-20 text-slate-600 italic">
              No technical logs recorded for this session.
            </div>
          ) : (
            logs.map((log) => {
              const isExpanded = expandedLogs.has(log.id) || log.level === 'ERROR';
              const hasData = log.data && Object.keys(log.data).length > 0;

              return (
                <div 
                  key={log.id} 
                  className={`block py-1 border-b border-slate-800/30 transition-colors ${hasData ? 'cursor-pointer hover:bg-slate-800/20' : ''}`}
                  onClick={() => hasData && toggleLog(log.id)}
                >
                  <div className="grid grid-cols-[64px_24px_1fr] gap-1 items-start group">
                    <span className="text-slate-600 whitespace-nowrap pt-0.5">
                      {new Date(log.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                    </span>
                    
                    <div className={`mt-0.5 flex items-center justify-center`}>
                      {log.level === 'ERROR' ? (
                        <AlertCircle className="w-3.5 h-3.5 text-red-500" />
                      ) : log.level === 'SUCCESS' ? (
                        <CheckCircle className="w-3.5 h-3.5 text-green-500" />
                      ) : log.level === 'START' ? (
                        <Clock className="w-3.5 h-3.5 text-blue-400" />
                      ) : (
                        <div className="w-3.5 h-3.5 rounded-full bg-slate-700 flex items-center justify-center">
                          <div className="w-1.5 h-1.5 rounded-full bg-slate-400" />
                        </div>
                      )}
                    </div>

                    <div className="min-w-0 flex-1">
                      <div className="flex items-center flex-wrap gap-x-2 gap-y-1">
                        {hasData && (
                          <span className="text-slate-500 flex-shrink-0">
                            {isExpanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                          </span>
                        )}
                        <span className={`font-black uppercase tracking-tighter text-[10px] break-all ${
                          log.level === 'ERROR' ? 'text-red-400' : 
                          log.level === 'SUCCESS' ? 'text-green-400' : 
                          'text-slate-400'
                        }`}>
                          [{log.task_name}]
                        </span>
                        {log.stage && (
                          <span className="px-1.5 py-0.5 bg-slate-800 rounded text-slate-400 font-bold uppercase text-[9px] break-all">
                            {log.stage}
                          </span>
                        )}
                      </div>
                      <p className={`mt-1 leading-relaxed break-all whitespace-pre-wrap ${
                        log.level === 'ERROR' ? 'text-red-300' : 
                        log.level === 'SUCCESS' ? 'text-green-300' : 
                        'text-slate-300'
                      }`}>
                        {log.message}
                      </p>
                      
                      {hasData && isExpanded && (
                        <div 
                          className={`mt-2 p-3 bg-black/40 rounded-xl border relative group/data transition-all ${
                            log.level === 'ERROR' ? 'border-red-900/50' : 'border-slate-800/50'
                          }`}
                          onClick={(e) => e.stopPropagation()}
                        >
                          <button
                            onClick={() => {
                              const header = `[${log.task_name}]${log.stage ? ` ${log.stage}` : ''}\nMessage: ${log.message}`;
                              const fullText = `${header}\n\nData:\n${JSON.stringify(log.data, null, 2)}`;
                              copyToClipboard(fullText, log.id);
                            }}
                            className="absolute top-2 right-2 p-1.5 bg-slate-800 hover:bg-slate-700 rounded-md text-slate-400 hover:text-slate-200 transition-colors opacity-0 group-hover/data:opacity-100"
                            title="Copy to clipboard"
                          >
                            {copiedId === log.id ? <Check className="w-3.5 h-3.5 text-green-400" /> : <Copy className="w-3.5 h-3.5" />}
                          </button>
                          <pre className={`text-[10px] whitespace-pre-wrap break-all no-scrollbar ${
                            log.level === 'ERROR' ? 'text-red-400' : 'text-slate-500'
                          }`}>
                            {JSON.stringify(log.data, null, 2)}
                          </pre>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-slate-800 bg-slate-900/30 flex items-center justify-between text-[10px] font-bold text-slate-500 uppercase tracking-widest">
          <div className="flex items-center space-x-4">
            <div className="flex items-center space-x-1">
              <div className="w-2 h-2 rounded-full bg-green-500/20 border border-green-500/40" />
              <span>Live Monitor</span>
            </div>
            <span>{logs.length} Log Entries</span>
          </div>
          <span>Health Assistant AI Runtime</span>
        </div>
      </div>
    </div>
  );
}
