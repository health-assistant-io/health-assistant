import React from 'react';
import { X, Zap } from 'lucide-react';
import { Portal } from '../ui/Portal';
import DisplayBlockRenderer from './displayBlocks';
import type { ActionResult } from '../../services/integrationService';

interface Props {
  result: ActionResult;
  actionLabel?: string;
  onClose: () => void;
}

const ActionResultModal: React.FC<Props> = ({ result, actionLabel, onClose }) => {
  const blocks = result.results || [];

  return (
    <Portal>
      <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-in fade-in duration-200">
        <div className="bg-white dark:bg-dark-surface w-full max-w-4xl rounded-2xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200 flex flex-col max-h-[90vh] z-[10000]">
          <div className="p-6 border-b border-gray-100 dark:border-dark-border flex items-center justify-between">
            <h3 className="flex items-center text-lg font-bold text-gray-900 dark:text-dark-text">
              <Zap className="w-5 h-5 mr-2 text-yellow-500" />
              {actionLabel ? `${actionLabel} — Result` : 'Action Result'}
            </h3>
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors"
            >
              <X className="w-6 h-6" />
            </button>
          </div>

          <div className="p-6 overflow-y-auto space-y-6">
            {result.message && (
              <div className="p-4 bg-blue-50 dark:bg-blue-900/20 border border-blue-100 dark:border-blue-800 rounded-xl">
                <p className="text-sm text-blue-700 dark:text-blue-300 font-medium">
                  {result.message}
                </p>
              </div>
            )}

            {blocks.length > 0 ? (
              blocks.map((block, idx) => (
                <div key={idx}>
                  {block.title && (
                    <h4 className="text-xs font-black text-gray-500 dark:text-dark-muted uppercase tracking-widest mb-2">
                      {block.title}
                    </h4>
                  )}
                  <DisplayBlockRenderer block={block} />
                </div>
              ))
            ) : (
              !result.message && (
                <p className="text-sm text-gray-400 italic text-center py-4">
                  Action completed. No structured result returned.
                </p>
              )
            )}
          </div>

          <div className="p-6 bg-gray-50 dark:bg-dark-border/30 flex items-center justify-end">
            <button
              onClick={onClose}
              className="px-6 py-2 text-sm font-bold text-white bg-blue-600 hover:bg-blue-700 rounded-xl transition-all shadow-md shadow-blue-200/50 dark:shadow-none active:scale-95"
            >
              Done
            </button>
          </div>
        </div>
      </div>
    </Portal>
  );
};

export default ActionResultModal;
