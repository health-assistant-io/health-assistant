import React from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Info, X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export interface BiomarkerInfoModalProps {
  info: string | null;
  name: string;
  onClose: () => void;
  slug?: string;
}

export const BiomarkerInfoModal: React.FC<BiomarkerInfoModalProps> = ({ info, name, onClose, slug }) => {
  const { t } = useTranslation();
  return createPortal(
  <div 
    className="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
    onClick={(e) => { e.preventDefault(); e.stopPropagation(); onClose(); }}
  >
    <div 
      className="bg-white dark:bg-dark-surface w-full max-w-lg rounded-3xl shadow-2xl overflow-hidden animate-in fade-in zoom-in duration-200"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="p-6 border-b border-gray-100 dark:border-dark-border flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div className="p-2 bg-blue-50 dark:bg-blue-900/30 rounded-xl">
            <Info className="w-5 h-5 text-blue-600" />
          </div>
          <div>
            <h3 className="text-xl font-bold text-gray-900 dark:text-dark-text">{name}</h3>
            {slug && <p className="text-xs text-gray-400 font-mono tracking-tighter uppercase">{slug}</p>}
          </div>
        </div>
        <button 
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); onClose(); }} 
          className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors"
        >
          <X className="w-5 h-5 text-gray-400" />
        </button>
      </div>
      <div className="p-8 max-h-[60vh] overflow-y-auto no-scrollbar">
        <div className="prose dark:prose-invert max-w-none">
          {info ? (
            info.includes('</') || info.includes('<br') ? (
              <div 
                className="text-gray-700 dark:text-dark-text leading-relaxed"
                dangerouslySetInnerHTML={{ __html: info }}
              />
            ) : (
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{info}</ReactMarkdown>
            )
          ) : (
            <p className="text-gray-500 italic">No information available.</p>
          )}
        </div>
      </div>
      <div className="p-6 bg-gray-50 dark:bg-dark-bg border-t border-gray-100 dark:border-dark-border flex justify-end">
        <button 
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); onClose(); }}
          className="px-8 py-2.5 bg-[#1a2b4b] text-white rounded-xl font-bold text-sm hover:bg-black transition-all shadow-md active:scale-95"
        >
          {t('common.dismiss')}
        </button>
      </div>
    </div>
  </div>,
  document.body
  );
};
