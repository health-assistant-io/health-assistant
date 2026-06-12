import React from 'react';
import { Activity, Baby, AlertTriangle, Zap, Scissors, Smile, Eye, Sparkles, CheckCircle, CheckCircle2, History, Clock } from 'lucide-react';
import { ClinicalEventStatus } from '../services/clinicalEventService';
import { TFunction } from 'i18next';

export const getEventIcon = (slug: string, className: string = "w-5 h-5") => {
  switch (slug) {
    case 'pain-episode': return <Activity className={className} />;
    case 'pregnancy': return <Baby className={className} />;
    case 'accident': return <AlertTriangle className={className} />;
    case 'flare-up': return <Zap className={className} />;
    case 'surgical-recovery': return <Scissors className={className} />;
    case 'dental': return <Smile className={className} />;
    case 'vision': return <Eye className={className} />;
    case 'aesthetic': return <Sparkles className={className} />;
    case 'maintenance': return <CheckCircle className={className} />;
    case 'reproductive-health': return <Baby className={className} />;
    case 'acute-chronic': return <Activity className={className} />;
    case 'specialized-care': return <Activity className={className} />;
    case 'routine-wellness': return <CheckCircle className={className} />;
    default: return <Activity className={className} />;
  }
};

export const getEventStatusBadge = (status: ClinicalEventStatus, t: TFunction, compact: boolean = false) => {
  if (compact) {
    switch (status) {
      case ClinicalEventStatus.ACTIVE:
        return <span className="text-green-600 dark:text-green-400 text-[10px] font-black uppercase tracking-widest flex items-center"><CheckCircle2 className="w-3 h-3 mr-1" /> {t('events.status.active')}</span>;
      case ClinicalEventStatus.RESOLVED:
        return <span className="text-gray-500 dark:text-dark-muted text-[10px] font-black uppercase tracking-widest flex items-center"><History className="w-3 h-3 mr-1" /> {t('events.status.resolved')}</span>;
      case ClinicalEventStatus.ON_HOLD:
        return <span className="text-yellow-600 dark:text-yellow-400 text-[10px] font-black uppercase tracking-widest flex items-center"><Clock className="w-3 h-3 mr-1" /> {t('events.status.on_hold')}</span>;
      default:
        return <span className="text-gray-400 text-[10px] font-black uppercase tracking-widest">{t('events.status.unknown')}</span>;
    }
  }

  switch (status) {
    case ClinicalEventStatus.ACTIVE:
      return <span className="px-3 py-1 bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400 border border-green-100 dark:border-green-800 rounded-full text-xs font-bold uppercase tracking-tight flex items-center space-x-1"><CheckCircle2 className="w-3.5 h-3.5 mr-1" /> {t('events.status.active')}</span>;
    case ClinicalEventStatus.RESOLVED:
      return <span className="px-3 py-1 bg-gray-50 dark:bg-dark-bg text-gray-500 dark:text-dark-muted border border-gray-200 dark:border-dark-border rounded-full text-xs font-bold uppercase tracking-tight flex items-center space-x-1"><History className="w-3.5 h-3.5 mr-1" /> {t('events.status.resolved')}</span>;
    case ClinicalEventStatus.ON_HOLD:
      return <span className="px-3 py-1 bg-yellow-50 dark:bg-yellow-900/20 text-yellow-600 dark:text-yellow-400 border border-yellow-100 dark:border-yellow-800 rounded-full text-xs font-bold uppercase tracking-tight flex items-center space-x-1"><Clock className="w-3.5 h-3.5 mr-1" /> {t('events.status.on_hold')}</span>;
    default:
      return <span className="px-3 py-1 bg-gray-50 dark:bg-dark-bg text-gray-400 rounded-full text-xs font-bold uppercase tracking-tight">{t('events.status.unknown')}</span>;
  }
};
