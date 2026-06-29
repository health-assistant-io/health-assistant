import React, { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { Activity, Info, ShieldAlert, Globe, Server } from 'lucide-react';
import { useActiveAIWorkflow, ActiveAITask } from '../../hooks/useActiveAIWorkflow';
import { useTranslation } from 'react-i18next';
import { getCountryFlag } from '../../utils/countryUtils';

interface Props {
  className?: string;
  showText?: boolean;
  workflow?: string;
  taskType?: string | string[]; // Added back for backward compatibility with existing components
  label?: string;
  variant?: 'default' | 'white';
  /** Visual size — `sm` for tight headers/inline contexts, `md` (default) otherwise. */
  size?: 'sm' | 'md';
}

export const AIBadge: React.FC<Props> = ({ 
  className = '', 
  showText = true, 
  workflow,
  taskType,
  label,
  variant = 'default',
  size = 'md',
}) => {
  const { t } = useTranslation();
  
  // Use taskType directly if provided (backward compatibility), otherwise use workflow
  const resolvedWorkflow = workflow || (typeof taskType === 'string' ? taskType : (Array.isArray(taskType) ? taskType[0] : undefined));
  const tasks = useActiveAIWorkflow(resolvedWorkflow);
  const isMultipleTasks = tasks.length > 1;

  const [isOpen, setIsOpen] = useState(false);
  const [coords, setCoords] = useState({ top: 0, left: 0, width: 0, height: 0 });
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const updateCoords = () => {
    if (triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect();
      setCoords({
        top: rect.bottom,
        left: rect.left,
        width: rect.width,
        height: rect.height,
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
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Close on Escape and return focus to the trigger
  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setIsOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [isOpen]);

  const renderTaskInfo = (task: ActiveAITask, index: number) => {
    const taskIsLocal = task.model?.is_local ?? task.provider?.is_local ?? false;
    
    // Map internal task types to friendly names
    let friendlyTaskName = task.taskType;
    switch(task.taskType) {
      case 'ocr': friendlyTaskName = 'Document OCR & Extraction'; break;
      case 'nlp': friendlyTaskName = 'Clinical Data Structuring (NLP)'; break;
      case 'chat': friendlyTaskName = 'AI Assistant'; break;
      case 'fill_biomarker_form': friendlyTaskName = 'Biomarker Extraction'; break;
      case 'magic_fill_examination': friendlyTaskName = 'Smart Examination Extraction'; break;
      case 'medication_interaction': friendlyTaskName = 'Medication Safety Audit'; break;
      case 'anomaly_detection': friendlyTaskName = 'Trend & Anomaly Detection'; break;
      case 'define_biomarker': friendlyTaskName = 'Definition Builder'; break;
      case 'define_medication': friendlyTaskName = 'Definition Builder'; break;
    }
    
    return (
      <div key={index} className={`space-y-4 ${index > 0 ? 'pt-4 border-t border-gray-100 dark:border-dark-border' : ''}`}>
        {isMultipleTasks && (
           <h5 className="text-[9px] font-black text-indigo-500 uppercase tracking-widest bg-indigo-50 dark:bg-indigo-900/30 px-2 py-1 rounded inline-block">
             Task: {friendlyTaskName}
           </h5>
        )}
        {/* Model Info */}
        <div className="flex flex-col gap-1">
          <span className="text-[9px] font-black uppercase text-gray-400 tracking-widest">Active Model</span>
          <span className="text-sm font-bold text-gray-900 dark:text-dark-text">{task.model?.name || task.model?.model_name || 'System Default'}</span>
        </div>

        {/* Provider Details */}
        {task.provider ? (
          <div className="bg-gray-50 dark:bg-dark-bg rounded-lg border border-gray-100 dark:border-dark-border overflow-hidden">
            <div className="p-3 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-[9px] font-black uppercase text-gray-400 tracking-widest">Provider</span>
                {taskIsLocal ? (
                  <span className="flex items-center gap-1 text-[9px] font-black uppercase tracking-widest text-emerald-600 bg-emerald-50 dark:bg-emerald-900/30 px-1.5 py-0.5 rounded">
                    <Server className="w-3 h-3" /> Local
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-[9px] font-black uppercase tracking-widest text-blue-600 bg-blue-50 dark:bg-blue-900/30 px-1.5 py-0.5 rounded">
                    <Globe className="w-3 h-3" /> Cloud
                  </span>
                )}
              </div>
              <div className="text-sm font-bold text-gray-800 dark:text-gray-200">
                {task.provider.company_name || task.provider.name} {getCountryFlag(task.provider.company_country)}
              </div>
              {task.provider.company_website && (
                <a href={task.provider.company_website} target="_blank" rel="noreferrer" className="text-[10px] text-blue-500 hover:underline truncate block">
                  {task.provider.company_website}
                </a>
              )}
            </div>
            
            {/* Task-specific Privacy Notice integrated into provider card */}
            <div className={`p-3 border-t border-gray-100 dark:border-dark-border ${taskIsLocal ? 'bg-emerald-50/50 dark:bg-emerald-900/10' : 'bg-blue-50/50 dark:bg-blue-900/10'}`}>
              <div className="flex items-start gap-2">
                {taskIsLocal ? (
                  <Server className="w-4 h-4 text-emerald-500 shrink-0 mt-0.5" />
                ) : (
                  <Globe className="w-4 h-4 text-blue-500 shrink-0 mt-0.5" />
                )}
                <p className={`text-[9px] leading-relaxed ${taskIsLocal ? 'text-emerald-700 dark:text-emerald-300' : 'text-blue-700 dark:text-blue-300'}`}>
                  <strong className="block mb-0.5 font-bold">{t('ai_disclaimer.privacy_notice_title', 'Privacy Notice')}</strong>
                  {taskIsLocal 
                    ? t('ai_disclaimer.privacy_local', 'This AI model runs locally. Your data does not leave your infrastructure.')
                    : t('ai_disclaimer.privacy_cloud', 'Data relevant to this context may be transmitted to and processed by 3rd-party LLM providers based on your system\'s AI configuration.')}
                </p>
              </div>
            </div>
          </div>
        ) : (
           <p className="text-xs text-gray-500 italic">No specific provider configured for this task.</p>
        )}
      </div>
    );
  };

  const popover = isOpen ? createPortal(
    <div
      ref={dropdownRef}
      className="fixed z-[99999] w-72 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200"
      style={{
        top: `${coords.top + 8}px`,
        // Center the popover relative to the badge, but ensure it doesn't go off screen
        left: `${Math.max(16, Math.min(window.innerWidth - 300, coords.left + (coords.width / 2) - 144))}px`,
      }}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="bg-indigo-50 dark:bg-indigo-900/20 px-4 py-3 border-b border-indigo-100 dark:border-indigo-900/30">
        <h4 className="text-xs font-black text-indigo-900 dark:text-indigo-300 uppercase tracking-widest flex items-center gap-2">
          <Activity className="w-4 h-4" />
          {label || t('ai_labels.ai_analysis', 'AI Analysis')}
        </h4>
        <p className="text-[10px] text-indigo-600/70 dark:text-indigo-400/70 mt-1 font-bold truncate uppercase tracking-widest">
          {workflow ? 'Workflow' : 'Task'}: {workflow ? workflow.replace(/_/g, ' ') : (taskType || 'default')}
        </p>
      </div>
      
      <div className="p-4">
        <div className="max-h-[60vh] overflow-y-auto pr-2 custom-scrollbar">
          {tasks.map((task, index) => renderTaskInfo(task, index))}
        </div>

        {/* Disclaimers */}
        <div className="mt-4 pt-3 border-t border-gray-100 dark:border-dark-border space-y-3">
           <div className="flex items-start gap-2 text-gray-600 dark:text-gray-400">
             <ShieldAlert className="w-4 h-4 text-amber-500 shrink-0 mt-0.5" />
             <p className="text-[10px] leading-relaxed">
               <strong className="text-gray-900 dark:text-gray-200 block mb-1">{t('ai_disclaimer.medical_warning_title', 'Medical Disclaimer')}</strong>
               {t('ai_disclaimer.medical_warning', 'This content is AI-generated and does not constitute professional medical advice. AI can hallucinate or omit critical information. Always verify with a certified healthcare provider.')}
             </p>
           </div>
        </div>
      </div>
    </div>,
    document.body
  ) : null;

  const variantClasses = variant === 'white' 
    ? "bg-white/10 text-white border-white/20 shadow-none hover:bg-white/20 hover:border-white/40"
    : "bg-indigo-50/30 dark:bg-indigo-900/10 text-indigo-600 dark:text-indigo-400 border border-indigo-100/50 dark:border-indigo-500/10 shadow-[0_2px_8px_-3px_rgba(79,70,229,0.08)] hover:shadow-[0_4px_12px_-2px_rgba(79,70,229,0.15)] hover:bg-white dark:hover:bg-dark-surface hover:border-indigo-300/50";

  const aiTextClasses = variant === 'white'
    ? "text-white font-black"
    : "bg-gradient-to-r from-indigo-600 via-purple-500 to-indigo-600 dark:from-indigo-400 dark:via-purple-400 dark:to-indigo-400 bg-[length:200%_auto] animate-gradient-x bg-clip-text text-transparent font-black";

  const infoIconClasses = variant === 'white'
    ? "text-white/70 group-hover:text-white"
    : "text-indigo-400 group-hover:text-indigo-600 dark:group-hover:text-indigo-300";

  const sizeClasses = size === 'sm'
    ? "px-2.5 py-0.5 text-xs"
    : "px-3.5 py-1.5 text-sm";

  const iconSize = size === 'sm' ? "w-3.5 h-3.5" : "w-4 h-4";

  return (
    <>
      <button
        type="button"
        ref={triggerRef}
        onClick={(e) => {
          e.stopPropagation();
          setIsOpen(!isOpen);
        }}
        aria-expanded={isOpen}
        aria-haspopup="dialog"
        aria-label={t('ai_labels.transparency_title', 'View AI Transparency Info')}
        title={t('ai_labels.transparency_title', 'View AI Transparency Info')}
        className={`inline-flex items-center space-x-1.5 ${sizeClasses} font-bold rounded-full transition-all active:scale-95 group ${variantClasses} ${className}`}
      >
        {showText && <span className={`tracking-tight ${aiTextClasses}`}>{label || 'AI'}</span>}
        <Info className={`${iconSize} transition-colors shrink-0 ${infoIconClasses}`} aria-hidden="true" />
      </button>
      {popover}
    </>
  );
};
