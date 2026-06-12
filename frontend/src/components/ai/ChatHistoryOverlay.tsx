import React, { useState } from 'react';
import { History, X, Calendar, Plus, Trash2, ChevronLeft, Search } from 'lucide-react';
import { format } from 'date-fns';
import { ChatSession } from '../../services/aiAssistanceService';
import { useTranslation } from 'react-i18next';

interface ChatHistoryOverlayProps {
  isOpen: boolean;
  onClose: () => void;
  sessions: ChatSession[];
  currentSessionId: string | null;
  onLoadSession: (id: string) => void;
  onDeleteSession: (e: React.MouseEvent, id: string) => void;
  onStartNewChat: () => void;
  isFullScreen: boolean;
}

export const ChatHistoryOverlay: React.FC<ChatHistoryOverlayProps> = ({
  isOpen,
  onClose,
  sessions,
  currentSessionId,
  onLoadSession,
  onDeleteSession,
  onStartNewChat,
  isFullScreen
}) => {
  const { t } = useTranslation();
  const [searchQuery, setSearchQuery] = useState('');

  if (!isFullScreen || !isOpen) return null;

  const filteredSessions = sessions.filter(session => 
    (session.title || t('ai_chat.status.untitled_consultation'))
      .toLowerCase()
      .includes(searchQuery.toLowerCase())
  );

  const sidebarClasses = isFullScreen 
    ? `lg:relative fixed inset-y-0 left-0 z-[1050] w-72 sm:w-80 bg-white dark:bg-dark-bg border-r border-gray-100 dark:border-dark-border shadow-2xl lg:shadow-none transition-all duration-300 ease-in-out transform ${isOpen ? 'translate-x-0' : '-translate-x-full lg:hidden'}`
    : `absolute inset-0 z-[200] animate-in slide-in-from-right duration-300 flex flex-col bg-white dark:bg-dark-surface`;

  return (
    <>
      {/* Backdrop for Mobile overlay mode */}
      {isOpen && isFullScreen && (
        <div 
          className="fixed inset-0 lg:hidden bg-black/40 backdrop-blur-sm z-[1040] animate-in fade-in duration-500 cursor-pointer"
          onClick={onClose}
        />
      )}

      <div className={`${sidebarClasses} flex flex-col relative`}>
        {/* Close Hook Button (Vertical handle on the right) */}
        {isFullScreen && isOpen && (
          <button
            onClick={onClose}
            className="absolute left-full top-1/2 -translate-y-1/2 z-[500] group flex items-center"
          >
            <div className="bg-white dark:bg-dark-bg border border-l-0 border-gray-100 dark:border-dark-border py-8 px-1 rounded-r-2xl shadow-xl hover:bg-indigo-50 dark:hover:bg-indigo-900/20 transition-all flex flex-col items-center gap-4">
               <ChevronLeft className="w-4 h-4 text-indigo-600 dark:text-indigo-400 group-hover:scale-110 transition-transform" />
               <div className="[writing-mode:vertical-lr] rotate-180 text-[9px] font-black uppercase tracking-[0.3em] text-gray-400 dark:text-dark-muted group-hover:text-indigo-600 transition-colors">
                 Hide
               </div>
            </div>
          </button>
        )}
        <div className={`px-6 py-5 border-b flex items-center justify-between ${isFullScreen ? 'border-gray-100 dark:border-dark-border' : 'border-gray-50 dark:border-dark-border'}`}>
          <div className="flex items-center gap-3">
            <div className="p-2 bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 rounded-xl">
               <History className="w-5 h-5" />
            </div>
            <h3 className={`text-xs font-black uppercase tracking-[0.2em] ${isFullScreen ? 'text-gray-900 dark:text-white' : 'text-gray-900 dark:text-dark-text'}`}>
              {t('ai_chat.history.title')}
            </h3>
          </div>
          {!isFullScreen && (
            <button 
              onClick={onClose} 
              className="p-2 hover:bg-gray-100 dark:hover:bg-dark-surface rounded-full transition-colors text-gray-400 hover:text-gray-600 dark:hover:text-white"
            >
              <X className="w-5 h-5" />
            </button>
          )}
        </div>
        
        <div className="p-4 border-b border-gray-50 dark:border-white/5">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input 
              type="text"
              placeholder={t('common.search')}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-9 pr-4 py-2 bg-gray-50 dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-xl text-xs focus:ring-2 focus:ring-indigo-500/20 transition-all outline-none"
            />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4 custom-scrollbar">
            <button 
              onClick={onStartNewChat} 
              className="w-full mb-6 p-4 border-2 border-dashed border-gray-100 dark:border-dark-border rounded-2xl flex items-center justify-center gap-3 text-slate-400 hover:border-indigo-500 hover:text-indigo-600 dark:hover:text-indigo-400 transition-all group"
            >
               <Plus className="w-5 h-5 group-hover:rotate-90 transition-transform" />
               <span className="text-[10px] font-black uppercase tracking-widest">{t('ai_chat.tooltips.new_chat')}</span>
            </button>

            <div className="space-y-3">
              {filteredSessions.map(session => (
                 <div 
                   key={session.id}
                   onClick={() => onLoadSession(session.id)}
                   className={`w-full group p-4 rounded-2xl border transition-all cursor-pointer ${
                     currentSessionId === session.id 
                       ? 'bg-indigo-600 border-indigo-500 text-white shadow-lg' 
                       : (isFullScreen ? 'bg-white dark:bg-dark-surface/50 border-gray-100 dark:border-dark-border text-gray-700 dark:text-slate-300 hover:bg-gray-50 dark:hover:bg-dark-surface' : 'bg-white dark:bg-dark-surface border-gray-100 dark:border-dark-border hover:border-indigo-100 dark:hover:border-indigo-900/50')
                   }`}
                 >
                    <div className="flex items-start justify-between gap-3">
                       <div className="flex-1 min-w-0">
                          <h4 className="text-[11px] font-black uppercase tracking-tight truncate mb-1">{session.title || t('ai_chat.status.untitled_consultation')}</h4>
                          <div className="flex items-center gap-1.5 opacity-60">
                             <Calendar className="w-2.5 h-2.5" />
                             <span className="text-[9px] font-black uppercase">{format(new Date(session.updated_at), 'MMM d, yyyy')}</span>
                          </div>
                       </div>
                       <button 
                         onClick={(e) => onDeleteSession(e, session.id)} 
                         className={`p-1.5 rounded-lg transition-all ${currentSessionId === session.id ? 'hover:bg-white/20 text-white' : 'opacity-0 group-hover:opacity-100 hover:bg-red-50 hover:text-red-500'}`}
                       >
                         <Trash2 className="w-3.5 h-3.5" />
                       </button>
                    </div>
                 </div>
              ))}
              {filteredSessions.length === 0 && searchQuery && (
                <div className="text-center py-10 text-gray-400">
                  <Search className="w-8 h-8 mx-auto mb-3 opacity-20" />
                  <p className="text-[10px] font-black uppercase tracking-widest">{t('common.no_results')}</p>
                </div>
              )}
            </div>
        </div>
      </div>
    </>
  );
};
