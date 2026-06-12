import React, { useState, useRef, useEffect, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { Sparkles, MessageSquare, Send, LucideIcon, X } from 'lucide-react';
import { useUIStore } from '../../store/slices/uiSlice';

export interface AIAction {
  label: string;
  description?: string;
  icon: LucideIcon;
  prompt: string;
  color?: string;
}

interface Props {
  actions: AIAction[];
  contextId: string;
  contextType: 'examination' | 'biomarker' | 'medication';
  title?: string;
  className?: string;
  align?: 'left' | 'right';
}

export const AIActionsDropdown: React.FC<Props> = ({ 
  actions,
  contextId,
  contextType,
  title = "AI Actions",
  className = "",
  align = 'right'
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [isQuerying, setIsQuerying] = useState(false);
  const [userQuery, setUserQuery] = useState('');
  const [coords, setCoords] = useState({ top: 0, left: 0, width: 0 });
  const dropdownRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  
  const setPendingAIMessage = useUIStore(state => state.setPendingAIMessage);
  const setAIDrawerOpen = useUIStore(state => state.setAIDrawerOpen);
  const setCurrentExaminationId = useUIStore(state => state.setCurrentExaminationId);
  const setCurrentBiomarkerId = useUIStore(state => state.setCurrentBiomarkerId);
  const setCurrentMedicationId = useUIStore(state => state.setCurrentMedicationId);

  const updateCoords = () => {
    if (triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect();
      setCoords({
        top: rect.bottom,
        left: rect.left,
        width: rect.width
      });
    }
  };

  useEffect(() => {
    if (isOpen) {
      updateCoords();
      window.addEventListener('scroll', updateCoords, true);
      window.addEventListener('resize', updateCoords);
    }
    return () => {
      window.removeEventListener('scroll', updateCoords, true);
      window.removeEventListener('resize', updateCoords);
    };
  }, [isOpen]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node) &&
          triggerRef.current && !triggerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
        setIsQuerying(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleAction = (prompt: string) => {
    // Set appropriate context
    if (contextType === 'examination') setCurrentExaminationId(contextId);
    if (contextType === 'biomarker') setCurrentBiomarkerId(contextId);
    if (contextType === 'medication') setCurrentMedicationId(contextId);
    
    setPendingAIMessage(prompt);
    setAIDrawerOpen(true);
    setIsOpen(false);
    setIsQuerying(false);
  };

  const handleCustomQuery = (e: React.FormEvent) => {
    e.preventDefault();
    if (!userQuery.trim()) return;
    handleAction(userQuery.trim());
    setUserQuery('');
  };

  const dropdownMenu = useMemo(() => {
    if (!isOpen) return null;

    return createPortal(
      <div 
        ref={dropdownRef}
        className={`fixed w-72 bg-white dark:bg-dark-surface border border-indigo-100 dark:border-dark-border rounded-2xl shadow-2xl z-[9999] py-2 animate-in zoom-in-95 duration-200`}
        style={{
          top: `${coords.top + 8}px`,
          left: align === 'right' ? `${coords.left - 288 + coords.width}px` : `${coords.left}px`,
        }}
        onClick={e => e.stopPropagation()}
      >
        <div className="px-4 py-2 border-b border-gray-50 dark:border-dark-border flex items-center justify-between text-indigo-600 dark:text-indigo-400">
          <div className="flex items-center space-x-2">
            <Sparkles className="w-3.5 h-3.5" />
            <span className="text-[10px] font-black uppercase tracking-widest text-gray-500">{title}</span>
          </div>
          <button onClick={() => setIsOpen(false)} className="p-1 hover:bg-gray-100 dark:hover:bg-dark-border rounded-full lg:hidden">
            <X className="w-3 h-3" />
          </button>
        </div>

        {!isQuerying ? (
          <div className="p-1">
            {actions.map((action, idx) => (
              <button
                key={idx}
                onClick={() => handleAction(action.prompt)}
                className="w-full flex items-center space-x-3 px-4 py-3 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 rounded-xl transition-colors text-left group"
              >
                <div className={`p-2 rounded-lg ${action.color || 'bg-blue-50 dark:bg-blue-900/40 text-blue-600'}`}>
                  <action.icon className="w-4 h-4" />
                </div>
                <div>
                  <p className="text-xs font-bold text-gray-900 dark:text-dark-text">{action.label}</p>
                  {action.description && <p className="text-[9px] text-gray-400 font-medium">{action.description}</p>}
                </div>
              </button>
            ))}

            <button
              onClick={() => setIsQuerying(true)}
              className="w-full flex items-center space-x-3 px-4 py-3 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 rounded-xl transition-colors text-left group"
            >
              <div className="p-2 bg-green-50 dark:bg-green-900/40 rounded-lg text-green-600">
                <MessageSquare className="w-4 h-4" />
              </div>
              <div>
                <p className="text-xs font-bold text-gray-900 dark:text-dark-text">Ask Specific Question...</p>
                <p className="text-[9px] text-gray-400 font-medium">Custom query for this {contextType}</p>
              </div>
            </button>
          </div>
        ) : (
          <div className="p-4 animate-in slide-in-from-right-2 duration-200">
            <form onSubmit={handleCustomQuery}>
              <label className="block text-[10px] font-black text-gray-400 uppercase tracking-widest mb-2 px-1">Your Question</label>
              <textarea
                autoFocus
                rows={3}
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-xs text-gray-900 dark:text-dark-text placeholder-gray-400 focus:ring-1 focus:ring-indigo-500/50 resize-none shadow-inner"
                placeholder={`e.g. Tell me more about this ${contextType}...`}
                value={userQuery}
                onChange={(e) => setUserQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleCustomQuery(e);
                  }
                }}
              />
              <div className="flex space-x-2 mt-3">
                <button 
                  type="button" 
                  onClick={() => setIsQuerying(false)}
                  className="flex-1 py-2 text-[10px] font-black uppercase text-gray-400 hover:text-gray-600 transition-colors"
                >
                  Back
                </button>
                <button 
                  type="submit"
                  disabled={!userQuery.trim()}
                  className="flex-[2] py-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white rounded-xl text-[10px] font-black uppercase tracking-widest transition-all flex items-center justify-center space-x-2 shadow-lg shadow-indigo-500/20"
                >
                  <Send className="w-3 h-3" />
                  <span>Ask AI</span>
                </button>
              </div>
            </form>
          </div>
        )}
      </div>,
      document.body
    );
  }, [isOpen, isQuerying, userQuery, coords, actions, contextType, title, align]);

  return (
    <div className={`relative ${className}`}>
      <button
        ref={triggerRef}
        onClick={(e) => {
          e.stopPropagation();
          setIsOpen(!isOpen);
          setIsQuerying(false);
        }}
        className="p-2 text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-900/30 rounded-xl transition-all border border-transparent hover:border-indigo-100 dark:hover:border-indigo-900/30 active:scale-95 group"
        title={title}
      >
        <Sparkles className="w-5 h-5 group-hover:rotate-12 transition-transform" />
      </button>

      {dropdownMenu}
    </div>
  );
};
