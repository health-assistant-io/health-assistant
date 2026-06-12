import React from 'react';
import { useTranslation } from 'react-i18next';
import { X, ExternalLink, Calendar, Clock, Info, ChevronRight, AlertTriangle, ShieldCheck, Tag, User, MapPin, Hash, Activity } from 'lucide-react';
import { format, parseISO, isValid } from 'date-fns';
import { Portal } from '../ui/Portal';

export interface SummaryModalAction {
  label: string;
  onClick: () => void;
  primary?: boolean;
  icon?: React.ElementType;
  variant?: 'primary' | 'secondary' | 'danger' | 'success';
}

export interface SummaryModalField {
  label: string;
  value: React.ReactNode;
  fullWidth?: boolean;
  icon?: React.ElementType;
  color?: string;
}

export interface SummaryModalStatus {
  label: string;
  type: 'success' | 'warning' | 'error' | 'info' | 'neutral';
  icon?: React.ElementType;
}

interface SummaryModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  type: 'medication' | 'allergy' | 'examination' | 'biomarker' | 'event' | 'document' | 'patient' | 'provider' | 'imaging';
  icon: React.ElementType;
  
  // Basic Metadata
  date?: string | Date;
  time?: string;
  status?: SummaryModalStatus;
  
  // Content
  fields?: SummaryModalField[];
  description?: string;
  descriptionLabel?: string;
  
  // Specialized Content
  tags?: string[];
  alert?: {
    message: string;
    type: 'critical' | 'warning' | 'info';
  };
  
  // Custom slots
  headerExtra?: React.ReactNode;
  footerExtra?: React.ReactNode;
  children?: React.ReactNode;
  
  // Actions
  mainAction?: SummaryModalAction;
  secondaryActions?: SummaryModalAction[];
}

export const SummaryModal: React.FC<SummaryModalProps> = ({
  isOpen,
  onClose,
  title,
  subtitle,
  type,
  icon: Icon,
  date,
  time,
  status,
  fields = [],
  description,
  descriptionLabel,
  tags = [],
  alert,
  headerExtra,
  footerExtra,
  mainAction,
  secondaryActions = [],
  children
}) => {
  const { t } = useTranslation();

  if (!isOpen) return null;

  const getTypeStyles = () => {
    switch (type) {
      case 'medication': return 'bg-blue-600';
      case 'allergy': return 'bg-red-600';
      case 'examination': return 'bg-indigo-600';
      case 'biomarker': return 'bg-emerald-600';
      case 'document': return 'bg-amber-600';
      case 'patient': return 'bg-teal-600';
      case 'provider': return 'bg-slate-700';
      case 'imaging': return 'bg-violet-600';
      default: return 'bg-gray-800';
    }
  };

  const getStatusStyles = (statusType: string) => {
    switch (statusType) {
      case 'success': return 'bg-green-50 text-green-700 border-green-100 dark:bg-green-900/20 dark:text-green-400 dark:border-green-900/30';
      case 'warning': return 'bg-yellow-50 text-yellow-700 border-yellow-100 dark:bg-yellow-900/20 dark:text-yellow-400 dark:border-yellow-900/30';
      case 'error': return 'bg-red-50 text-red-700 border-red-100 dark:bg-red-900/20 dark:text-red-400 dark:border-red-900/30';
      default: return 'bg-blue-50 text-blue-700 border-blue-100 dark:bg-blue-900/20 dark:text-blue-400 dark:border-blue-900/30';
    }
  };

  const getAlertStyles = (alertType: string) => {
    switch (alertType) {
      case 'critical': return 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-900/30 text-red-700 dark:text-red-400';
      case 'warning': return 'bg-amber-50 dark:bg-amber-900/20 border-amber-200 dark:border-amber-900/30 text-amber-700 dark:text-amber-400';
      default: return 'bg-blue-50 dark:bg-blue-900/20 border-blue-200 dark:border-blue-900/30 text-blue-700 dark:text-blue-400';
    }
  };

  const formattedDate = date ? (
    typeof date === 'string' ? (isValid(parseISO(date)) ? format(parseISO(date), 'MMMM d, yyyy') : date) : 
    (isValid(date) ? format(date, 'MMMM d, yyyy') : null)
  ) : null;

  return (
    <Portal>
      <div 
        className="fixed inset-0 bg-black/70 backdrop-blur-md z-[250] flex items-center justify-center p-4 animate-in fade-in duration-300 overflow-hidden cursor-pointer"
        onClick={onClose}
      >
        <div 
          className="bg-white dark:bg-dark-surface rounded-[2.5rem] w-full max-w-xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-300 border border-white/10 flex flex-col max-h-[90vh] cursor-default"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header Section */}
          <div className={`p-8 sm:p-10 text-white relative shrink-0 ${getTypeStyles()} overflow-hidden`}>
            {/* Background Decorative Icon */}
            <Icon className="absolute -right-8 -bottom-8 w-48 h-48 opacity-10 rotate-12 pointer-events-none" />
            
            <button 
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onClose();
              }}
              className="absolute top-6 right-6 p-2 bg-black/10 hover:bg-black/30 text-white rounded-full transition-all z-[100] hover:rotate-90 active:scale-90 shadow-lg border border-white/10"
              aria-label="Close modal"
            >
              <X className="w-5 h-5" />
            </button>
            
            <div className="flex items-start space-x-5 mb-2 relative z-10 pr-10">
              <div className="p-4 sm:p-5 bg-white/20 rounded-[1.5rem] backdrop-blur-xl border border-white/20 shadow-2xl shrink-0">
                <Icon className="w-8 h-8 sm:w-10 sm:h-10 text-white drop-shadow-lg" />
              </div>
              <div className="min-w-0 flex-1 pt-1">
                <h2 className="text-2xl sm:text-4xl font-black tracking-tighter truncate leading-[1.1] mb-1">{title}</h2>
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-white/70 text-[10px] sm:text-xs font-black uppercase tracking-[0.2em]">
                    {subtitle || `${type} ${t('common.details')}`}
                  </p>
                  {status && (
                    <span className={`px-2 py-0.5 rounded-lg text-[9px] font-black uppercase tracking-widest border border-white/20 bg-white/10`}>
                      {status.label}
                    </span>
                  )}
                </div>
              </div>
            </div>
            {headerExtra && <div className="mt-6 relative z-10">{headerExtra}</div>}
          </div>
          
          {/* Content Section */}
          <div className="p-8 sm:p-10 space-y-10 overflow-y-auto custom-scrollbar flex-1">
            {/* Critical Alert Slot */}
            {alert && (
              <div className={`p-5 rounded-3xl border flex items-start space-x-4 animate-pulse ${getAlertStyles(alert.type)}`}>
                <AlertTriangle className="w-6 h-6 shrink-0" />
                <div className="text-sm font-bold leading-relaxed">{alert.message}</div>
              </div>
            )}

            {/* Quick Stats Grid */}
            {(formattedDate || time || status) && (
              <div className="flex flex-wrap items-center gap-8">
                {formattedDate && (
                  <div className="flex items-center space-x-3">
                    <div className="p-2.5 bg-gray-50 dark:bg-dark-bg rounded-xl text-gray-400"><Calendar className="w-5 h-5" /></div>
                    <div className="flex flex-col">
                      <span className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest leading-none mb-1">{t('common.date')}</span>
                      <span className="text-base font-black text-gray-900 dark:text-dark-text leading-none">{formattedDate}</span>
                    </div>
                  </div>
                )}
                {time && (
                  <div className="flex items-center space-x-3">
                    <div className="p-2.5 bg-gray-50 dark:bg-dark-bg rounded-xl text-gray-400"><Clock className="w-5 h-5" /></div>
                    <div className="flex flex-col">
                      <span className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest leading-none mb-1">{t('common.time')}</span>
                      <span className="text-base font-black text-gray-900 dark:text-dark-text leading-none">{time}</span>
                    </div>
                  </div>
                )}
                {status && (
                  <div className="flex items-center space-x-3">
                    <div className={`p-2.5 rounded-xl border ${getStatusStyles(status.type)}`}>
                      {status.icon ? <status.icon className="w-5 h-5" /> : <ShieldCheck className="w-5 h-5" />}
                    </div>
                    <div className="flex flex-col">
                      <span className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest leading-none mb-1">{t('common.status')}</span>
                      <span className="text-base font-black text-gray-900 dark:text-dark-text leading-none uppercase tracking-tight">{status.label}</span>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Dynamic Fields Grid */}
            {fields.length > 0 && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-6 bg-gray-50/50 dark:bg-dark-bg/20 p-8 rounded-[2rem] border border-gray-100 dark:border-dark-border">
                {fields.map((field, idx) => (
                  <div key={idx} className={`${field.fullWidth ? 'col-span-1 sm:col-span-2' : 'col-span-1'} group`}>
                    <div className="flex items-center space-x-2 mb-2 px-1">
                      {field.icon && <field.icon className={`w-3.5 h-3.5 ${field.color || 'text-gray-400'}`} />}
                      <span className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">
                        {field.label}
                      </span>
                    </div>
                    <div className="text-sm sm:text-base font-bold text-gray-800 dark:text-dark-text leading-relaxed pl-1 transition-all group-hover:translate-x-1">
                      {field.value}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Description Section */}
            {description && (
              <div className="space-y-4">
                <h4 className="flex items-center space-x-2 text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest px-1">
                  <Info className="w-4 h-4 text-blue-500" />
                  <span>{descriptionLabel || t('common.info')}</span>
                </h4>
                <div className="p-8 bg-white dark:bg-dark-surface rounded-[2.5rem] border border-gray-100 dark:border-dark-border shadow-sm">
                  <p className="text-gray-700 dark:text-dark-text leading-[1.6] font-medium text-sm sm:text-base">
                    {description}
                  </p>
                </div>
              </div>
            )}

            {/* Tags / Badges Section */}
            {tags.length > 0 && (
              <div className="flex flex-wrap gap-2 pt-2">
                {tags.map((tag, idx) => (
                  <span key={idx} className="flex items-center space-x-1.5 px-3 py-1.5 bg-gray-100 dark:bg-dark-bg text-gray-500 dark:text-dark-muted rounded-xl text-[10px] font-black uppercase tracking-widest border border-gray-200 dark:border-dark-border">
                    <Tag className="w-3 h-3" />
                    <span>{tag}</span>
                  </span>
                ))}
              </div>
            )}

            {/* Custom Slot */}
            {children && <div className="pt-4">{children}</div>}
          </div>

          {/* Footer Section */}
          <div className="p-8 sm:p-10 bg-gray-50 dark:bg-dark-bg/40 border-t border-gray-100 dark:border-dark-border flex flex-col sm:flex-row justify-between items-center gap-6 shrink-0">
            <div className="w-full sm:w-auto flex flex-wrap gap-3">
               {secondaryActions.map((action, idx) => (
                <button 
                  key={idx}
                  onClick={action.onClick}
                  className={`flex-1 sm:flex-none px-6 py-3.5 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border text-gray-700 dark:text-dark-text font-black text-[11px] uppercase tracking-widest rounded-2xl hover:bg-gray-50 dark:hover:bg-dark-border transition-all shadow-sm active:scale-95 flex items-center justify-center space-x-2`}
                >
                  {action.icon && <action.icon className="w-4 h-4" />}
                  <span>{action.label}</span>
                </button>
              ))}
              {footerExtra}
            </div>
            
            <div className="w-full sm:w-auto flex flex-col sm:flex-row gap-3">
              {mainAction ? (
                <button 
                  onClick={mainAction.onClick}
                  className="w-full sm:w-auto px-10 py-4 bg-blue-600 text-white font-black text-xs uppercase tracking-widest rounded-2xl hover:bg-blue-700 transition-all shadow-lg shadow-blue-500/20 active:scale-95 flex items-center justify-center space-x-3 group"
                >
                  {mainAction.icon ? <mainAction.icon className="w-4 h-4" /> : <ExternalLink className="w-4 h-4" />}
                  <span>{mainAction.label}</span>
                  <ChevronRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
                </button>
              ) : (
                <button 
                  onClick={onClose}
                  className="px-12 py-4 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border text-gray-900 dark:text-dark-text font-black text-xs uppercase tracking-widest rounded-2xl hover:bg-gray-100 dark:hover:bg-dark-border transition-all shadow-sm active:scale-95"
                >
                  {t('common.dismiss')}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </Portal>
  );
};
