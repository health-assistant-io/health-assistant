import React from 'react';
import { Database, X, ChevronRight } from 'lucide-react';
import { Message, ToolCallInfo } from '../../types/ai';
import { useTranslation } from 'react-i18next';

interface ChatLedgerOverlayProps {
  isOpen: boolean;
  onClose: () => void;
  messages: Message[];
  onInspectTool: (tool: ToolCallInfo) => void;
  isFullScreen: boolean;
}

export const ChatLedgerOverlay: React.FC<ChatLedgerOverlayProps> = ({
  isOpen,
  onClose,
  messages,
  onInspectTool,
  isFullScreen
}) => {
  const { t } = useTranslation();

  if (!isOpen) return null;

  const finishedTools = Array.from(new Set(
    messages.flatMap(m => m.toolCalls || [])
    .filter(tc => tc.status === 'finished')
    .map(tc => tc.name)
  ));

  return (
    <div className={`absolute inset-0 z-[200] animate-in slide-in-from-right duration-300 flex flex-col ${isFullScreen ? 'bg-white dark:bg-dark-bg' : 'bg-white dark:bg-dark-surface'}`}>
      <div className={`px-8 py-6 border-b flex items-center justify-between ${isFullScreen ? 'border-gray-100 dark:border-dark-border' : 'border-gray-50 dark:border-dark-border'}`}>
        <div className="flex items-center gap-3">
          <div className="p-2 bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 rounded-xl">
             <Database className="w-5 h-5" />
          </div>
          <h3 className={`text-sm font-black uppercase tracking-[0.3em] ${isFullScreen ? 'text-gray-900 dark:text-white' : 'text-gray-900 dark:text-dark-text'}`}>
            {t('ai_chat.ledger.title')}
          </h3>
        </div>
        <button 
          onClick={onClose} 
          className="p-3 hover:bg-gray-100 dark:hover:bg-dark-surface rounded-full transition-colors text-gray-400 hover:text-gray-600 dark:hover:text-white"
        >
          <X className="w-6 h-6" />
        </button>
      </div>
      
      <div className="flex-1 overflow-y-auto p-10 custom-scrollbar">
         <div className="max-w-3xl mx-auto space-y-4">
            {finishedTools.map((toolName, idx) => (
              <button 
                key={idx}
                onClick={() => {
                  const latest = messages.flatMap(m => m.toolCalls || []).reverse().find(tc => tc.name === toolName && tc.status === 'finished');
                  if (latest) onInspectTool(latest);
                }}
                className={`w-full flex items-center justify-between p-6 rounded-[2rem] border transition-all ${
                  isFullScreen ? 'bg-white dark:bg-dark-surface border-gray-100 dark:border-dark-border hover:bg-gray-50 dark:hover:bg-dark-surface/80 text-gray-700 dark:text-slate-300' : 'bg-gray-50 dark:bg-dark-bg/50 border-gray-100 dark:border-dark-border hover:border-indigo-200 dark:hover:border-indigo-900/50'
                }`}
              >
                 <div className="flex items-center space-x-4">
                    <div className="w-3 h-3 rounded-full bg-emerald-500 shadow-[0_0_12px_rgba(16,185,129,0.4)]" />
                    <span className="text-xs font-black uppercase tracking-[0.2em]">{toolName.replace(/_/g, ' ')}</span>
                 </div>
                 <ChevronRight className="w-5 h-5 opacity-30" />
              </button>
            ))}
         </div>
      </div>
    </div>
  );
};
