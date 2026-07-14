import React, { useState } from 'react';
import { Settings, X, TrendingUp, Layers, Grid, Box, Activity } from 'lucide-react';
import { useTranslation } from 'react-i18next';

export type ChartType = 'line' | 'area' | 'bar';

export interface VisualizationSettingsProps {
  chartType: ChartType;
  setChartType: (t: ChartType) => void;
  showGrid: boolean;
  setShowGrid: (v: boolean) => void;
  showReferenceRanges: boolean;
  setShowReferenceRanges: (v: boolean) => void;
  showSpikes: boolean;
  setShowSpikes: (v: boolean) => void;
}

export const VisualizationSettings: React.FC<VisualizationSettingsProps> = ({
  chartType,
  setChartType,
  showGrid,
  setShowGrid,
  showReferenceRanges,
  setShowReferenceRanges,
  showSpikes,
  setShowSpikes,
}) => {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={`p-2.5 rounded-xl border transition-all shadow-sm active:scale-95 ${isOpen ? 'bg-blue-600 text-white border-blue-600' : 'bg-white dark:bg-dark-surface border-gray-200 dark:border-dark-border text-gray-700 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-bg'}`}
        title={t('biomarkers.visualization_settings')}
      >
        <Settings className={`w-5 h-5 ${isOpen ? 'animate-spin-slow' : ''}`} />
      </button>

      {isOpen && (
        <>
          <div className="fixed inset-0 z-[60]" onClick={() => setIsOpen(false)} />
          <div className="fixed sm:absolute inset-x-4 sm:inset-x-auto sm:right-0 top-1/2 -translate-y-1/2 sm:top-full sm:translate-y-0 mt-0 sm:mt-3 sm:w-80 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-[2rem] shadow-2xl z-[70] animate-in slide-in-from-top-4 duration-200">
            <div className="flex items-center justify-between p-6 pb-0">
              <h3 className="text-sm font-black text-brand-navy dark:text-dark-text uppercase tracking-widest">{t('biomarkers.visualization_settings')}</h3>
              <button onClick={() => setIsOpen(false)} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors">
                <X className="w-5 h-5 text-gray-400" />
              </button>
            </div>
            <div className="p-6 space-y-6">
              <div className="space-y-3">
                <p className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em]">{t('biomarkers.visualization_style')}</p>
                <div className="flex bg-gray-100 dark:bg-dark-bg p-1 rounded-2xl">
                  {[
                    { id: 'line', icon: TrendingUp, label: t('biomarkers.styles.line') },
                    { id: 'area', icon: Layers, label: t('biomarkers.styles.area') },
                    { id: 'bar', icon: Grid, label: t('biomarkers.styles.bar') },
                  ].map(type => (
                    <button key={type.id} onClick={() => setChartType(type.id as ChartType)} className={`flex-1 flex items-center justify-center space-x-2 py-2 rounded-xl text-xs font-bold transition-all ${chartType === type.id ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}>
                      <type.icon className="w-4 h-4" />
                      <span>{type.label}</span>
                    </button>
                  ))}
                </div>
              </div>
              <div className="space-y-3">
                <p className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em]">{t('biomarkers.display_overlays')}</p>
                <div className="space-y-2">
                  <button onClick={() => setShowGrid(!showGrid)} className={`w-full flex items-center justify-between px-4 py-3 rounded-2xl border transition-all ${showGrid ? 'bg-indigo-50 dark:bg-indigo-900/10 border-indigo-100 dark:border-indigo-900/30 text-indigo-600' : 'bg-gray-50 dark:bg-dark-bg border-transparent text-gray-500'}`}>
                    <div className="flex items-center space-x-3"><Layers className="w-4 h-4" /><span className="text-xs font-bold">{t('biomarkers.overlays.grid')}</span></div>
                    <div className={`w-8 h-4 rounded-full relative transition-colors ${showGrid ? 'bg-indigo-500' : 'bg-gray-300'}`}><div className={`absolute top-1 w-2 h-2 bg-white rounded-full transition-all ${showGrid ? 'left-5' : 'left-1'}`} /></div>
                  </button>
                  <button onClick={() => setShowReferenceRanges(!showReferenceRanges)} className={`w-full flex items-center justify-between px-4 py-3 rounded-2xl border transition-all ${showReferenceRanges ? 'bg-emerald-50 dark:bg-emerald-900/10 border-emerald-100 dark:bg-emerald-900/30 text-emerald-600' : 'bg-gray-50 dark:bg-dark-bg border-transparent text-gray-500'}`}>
                    <div className="flex items-center space-x-3"><Box className="w-4 h-4" /><span className="text-xs font-bold">{t('biomarkers.overlays.reference')}</span></div>
                    <div className={`w-8 h-4 rounded-full relative transition-colors ${showReferenceRanges ? 'bg-emerald-500' : 'bg-gray-300'}`}><div className={`absolute top-1 w-2 h-2 bg-white rounded-full transition-all ${showReferenceRanges ? 'left-5' : 'left-1'}`} /></div>
                  </button>
                  <button onClick={() => setShowSpikes(!showSpikes)} className={`w-full flex items-center justify-between px-4 py-3 rounded-2xl border transition-all ${showSpikes ? 'bg-rose-50 dark:bg-rose-900/10 border-rose-100 dark:border-rose-900/30 text-rose-600' : 'bg-gray-50 dark:bg-dark-bg border-transparent text-gray-500'}`}>
                    <div className="flex items-center space-x-3"><Activity className="w-4 h-4" /><span className="text-xs font-bold">{t('biomarkers.overlays.spikes', 'Show Min/Max Spikes')}</span></div>
                    <div className={`w-8 h-4 rounded-full relative transition-colors ${showSpikes ? 'bg-rose-500' : 'bg-gray-300'}`}><div className={`absolute top-1 w-2 h-2 bg-white rounded-full transition-all ${showSpikes ? 'left-5' : 'left-1'}`} /></div>
                  </button>
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
};
