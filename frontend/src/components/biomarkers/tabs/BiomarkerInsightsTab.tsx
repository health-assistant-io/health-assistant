/**
 * BiomarkerDetail "AI Insights" tab — the smart-analysis gradient card.
 * Extracted from the inline body. Currently a deterministic placeholder; will
 * wire into the analytics/anomaly-detection pipeline when that lands.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Activity } from 'lucide-react';
import { AIBadge } from '../../ui/AIBadge';
import type { Biomarker } from '../../../types/biomarker';

interface BiomarkerInsightsTabProps {
  biomarker: Biomarker;
}

export const BiomarkerInsightsTab: React.FC<BiomarkerInsightsTabProps> = ({ biomarker }) => {
  const { t } = useTranslation();

  return (
    <div className="p-8 animate-in fade-in duration-300">
      <div className="bg-gradient-to-br from-blue-600 to-indigo-700 rounded-[2rem] p-8 text-white shadow-xl shadow-blue-200 dark:shadow-none">
        <div className="flex items-center space-x-3 mb-6 flex-wrap">
          <div className="p-2 bg-white/20 rounded-xl backdrop-blur-md">
            <Activity className="w-6 h-6 text-white" />
          </div>
          <h3 className="text-xl font-black uppercase tracking-tight">{t('biomarkers.smart_analysis')}</h3>
          <AIBadge variant="white" taskType="anomaly_detection" />
        </div>
        <p className="text-blue-50 leading-relaxed mb-8 font-medium">
          Based on the longitudinal data for {biomarker.name}, the trend appears to be stable.
          Maintaining current lifestyle factors is recommended. Clinical context suggest that levels within this range are optimal for patients in your age demographic.
        </p>
        <div className="grid grid-cols-2 gap-4">
          <div className="bg-white/10 backdrop-blur-md p-5 rounded-2xl border border-white/10 shadow-inner">
            <p className="text-[10px] font-black text-blue-200 uppercase tracking-widest mb-1">{t('documents_explorer.status')}</p>
            <p className="text-lg font-black">Within Range</p>
          </div>
          <div className="bg-white/10 backdrop-blur-md p-5 rounded-2xl border border-white/10 shadow-inner">
            <p className="text-[10px] font-black text-blue-200 uppercase tracking-widest mb-1">Change</p>
            <p className="text-lg font-black">+0.4% (Steady)</p>
          </div>
        </div>
      </div>
    </div>
  );
};
