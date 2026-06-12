import React, { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { Activity, Info, ShieldAlert, Globe, Server } from 'lucide-react';
import { useActiveAITask } from '../../hooks/useActiveAITask';
import { useTranslation } from 'react-i18next';
import { getCountryFlag } from '../../utils/countryUtils';

interface Props {
  className?: string;
  showText?: boolean;
  taskType?: string;
  label?: string;
  variant?: 'default' | 'white';
}

export const AIBadge: React.FC<Props> = ({ 
  className = '', 
  showText = true, 
  taskType, 
  label,
  variant = 'default'
}) => {
  const { t } = useTranslation();
  const { provider, model } = useActiveAITask(taskType);
  const isLocal = model?.is_local ?? provider?.is_local ?? false;
  const [isOpen, setIsOpen] = useState(false);
  const [coords, setCoords] = useState({ top: 0, left: 0, width: 0, height: 0 });
  const triggerRef = useRef<HTMLSpanElement>(null);
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
        <p className="text-[10px] text-indigo-600/70 dark:text-indigo-400/70 mt-1 font-bold">
          Task: {taskType || 'default'}
        </p>
      </div>
      
      <div className="p-4 space-y-4">
        {/* Model Info */}
        <div className="flex flex-col gap-1">
          <span className="text-[9px] font-black uppercase text-gray-400 tracking-widest">Active Model</span>
          <span className="text-sm font-bold text-gray-900 dark:text-dark-text">{model?.name || model?.model_name || 'System Default'}</span>
        </div>

        {/* Provider Details */}
        {provider ? (
          <div className="bg-gray-50 dark:bg-dark-bg rounded-lg p-3 border border-gray-100 dark:border-dark-border space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-[9px] font-black uppercase text-gray-400 tracking-widest">Provider</span>
              {isLocal ? (
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
              {provider.company_name || provider.name} {getCountryFlag(provider.company_country)}
            </div>
            {provider.company_website && (
              <a href={provider.company_website} target="_blank" rel="noreferrer" className="text-[10px] text-blue-500 hover:underline truncate block">
                {provider.company_website}
              </a>
            )}
          </div>
        ) : (
           <p className="text-xs text-gray-500 italic">No specific provider configured for this task.</p>
        )}

        {/* Disclaimers */}
        <div className="mt-4 pt-3 border-t border-gray-100 dark:border-dark-border space-y-3">
           <div className="flex items-start gap-2 text-gray-600 dark:text-gray-400">
             <ShieldAlert className="w-4 h-4 text-amber-500 shrink-0 mt-0.5" />
             <p className="text-[10px] leading-relaxed">
               <strong className="text-gray-900 dark:text-gray-200 block mb-1">{t('ai_disclaimer.medical_warning_title', 'Medical Disclaimer')}</strong>
               {t('ai_disclaimer.medical_warning', 'This content is AI-generated and does not constitute professional medical advice. AI can hallucinate or omit critical information. Always verify with a certified healthcare provider.')}
             </p>
           </div>
           
           <div className="flex items-start gap-2 text-gray-600 dark:text-gray-400">
             {isLocal ? (
               <Server className="w-4 h-4 text-emerald-500 shrink-0 mt-0.5" />
             ) : (
               <Globe className="w-4 h-4 text-blue-500 shrink-0 mt-0.5" />
             )}
             <p className="text-[10px] leading-relaxed">
               <strong className="text-gray-900 dark:text-gray-200 block mb-1">{t('ai_disclaimer.privacy_notice_title', 'Privacy Notice')}</strong>
               {isLocal 
                 ? t('ai_disclaimer.privacy_local', 'This AI model runs locally. Your data does not leave your infrastructure.')
                 : t('ai_disclaimer.privacy_cloud', 'Data relevant to this context may be transmitted to and processed by 3rd-party LLM providers based on your system\'s AI configuration.')}
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

  return (
    <>
      <span 
        ref={triggerRef}
        onClick={(e) => {
          e.stopPropagation();
          setIsOpen(!isOpen);
        }}
        className={`inline-flex items-center space-x-2 px-3.5 py-1.5 text-sm font-bold rounded-full transition-all active:scale-95 group ${variantClasses} ${className}`}
        title="View AI Transparency Info"
      >
        {showText && <span className={`tracking-tight ${aiTextClasses}`}>{label || 'AI'}</span>}
        <Info className={`w-4 h-4 transition-colors shrink-0 ${infoIconClasses}`} />
      </span>
      {popover}
    </>
  );
};
