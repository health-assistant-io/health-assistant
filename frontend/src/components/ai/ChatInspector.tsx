import React from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { DataMiniPage } from './DataMiniPage';
import { ToolCallInfo } from '../../types/ai';
import { useTranslation } from 'react-i18next';

interface ChatInspectorProps {
  tool: ToolCallInfo;
  onClose: () => void;
  viewMode: 'raw' | 'table';
  onViewModeChange: (mode: 'raw' | 'table') => void;
}

export const ChatInspector: React.FC<ChatInspectorProps> = ({ 
  tool, 
  onClose, 
  viewMode, 
  onViewModeChange 
}) => {
  const { t } = useTranslation();

  return createPortal(
    <div className="fixed inset-0 z-[1100] flex items-center justify-center p-6 bg-black/80 backdrop-blur-md">
       <div className="bg-white dark:bg-dark-bg w-full max-w-4xl max-h-[85vh] rounded-[3rem] shadow-2xl border border-gray-200 dark:border-dark-border flex flex-col overflow-hidden animate-in zoom-in-95 duration-300">
           <div className="px-10 py-8 border-b border-gray-200 dark:border-dark-border flex items-center justify-between bg-gray-50/50 dark:bg-dark-surface/50">
              <div className="flex items-center gap-6">
                 <div>
                    <h3 className="text-xl font-black text-gray-900 dark:text-white uppercase tracking-tight">{t('ai_chat.inspector.title')}</h3>
                    <p className="text-[10px] font-black text-indigo-600 dark:text-indigo-400 uppercase tracking-widest mt-1">{tool.name}</p>
                 </div>
                  <div className="flex items-center bg-gray-200/50 dark:bg-dark-bg p-1 rounded-xl">
                     <button 
                       onClick={() => onViewModeChange('table')}
                       className={`px-3 py-1.5 rounded-lg text-[9px] font-black uppercase tracking-widest transition-all ${viewMode === 'table' ? 'bg-white dark:bg-dark-surface text-indigo-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                     >
                        Mini Page
                     </button>
                     <button 
                       onClick={() => onViewModeChange('raw')}
                       className={`px-3 py-1.5 rounded-lg text-[9px] font-black uppercase tracking-widest transition-all ${viewMode === 'raw' ? 'bg-white dark:bg-dark-surface text-indigo-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                     >
                        Raw JSON
                     </button>
                  </div>
              </div>
              <button onClick={onClose} className="p-3 bg-gray-100 dark:bg-dark-surface hover:bg-gray-200 dark:hover:bg-dark-border rounded-full transition-all text-gray-400 dark:text-slate-400">
                <X className="w-6 h-6" />
              </button>
           </div>
           <div className="flex-1 overflow-auto p-10 custom-scrollbar space-y-8">
              {/* Input Arguments */}
              <div>
                 <h4 className="text-[10px] font-black uppercase tracking-[0.3em] text-gray-400 dark:text-dark-muted mb-4 ml-4">Input Arguments</h4>
                 <div className="bg-gray-50 dark:bg-black/20 p-8 rounded-[2rem] border border-gray-100 dark:border-white/5 shadow-inner overflow-auto">
                     <pre className="text-xs font-mono text-indigo-600 dark:text-indigo-300 whitespace-pre-wrap leading-loose">
                         {(() => {
                             try {
                                 const parsed = typeof tool.args === 'string' ? JSON.parse(tool.args) : tool.args;
                                 return JSON.stringify(parsed, null, 4);
                             } catch (e) {
                                 return tool.args || '{}';
                             }
                         })()}
                     </pre>
                 </div>
              </div>

              {/* Result */}
              <div>
                 <h4 className="text-[10px] font-black uppercase tracking-[0.3em] text-gray-400 dark:text-dark-muted mb-4 ml-4">Execution Result</h4>
                 {viewMode === 'raw' ? (
                   <div className="bg-gray-50 dark:bg-black/40 p-8 rounded-[2rem] border border-gray-100 dark:border-white/5 shadow-inner overflow-auto">
                      <pre className="text-xs font-mono text-emerald-600 dark:text-emerald-400 whitespace-pre-wrap leading-loose">
                          {(() => {
                              try {
                                  const parsed = JSON.parse(tool.result || '');
                                  return JSON.stringify(parsed, null, 4);
                              } catch (e) {
                                  return tool.result;
                              }
                          })()}
                      </pre>
                   </div>
                 ) : (
                   <DataMiniPage 
                     data={(() => {
                        try {
                          return JSON.parse(tool.result || 'null');
                        } catch (e) {
                          return null;
                        }
                     })()} 
                     toolName={tool.name}
                     onClose={onClose}
                   />
                 )}
              </div>
           </div>
       </div>
    </div>,
    document.body
  );
};
