import React, { useState } from 'react';
import { X, Sparkles, Wand2, Loader2 } from 'lucide-react';
import { getAIAssistance } from '../../services/aiAssistanceService';
import { AIBadge } from './AIBadge';
import { useModalA11y } from '../../hooks/useModalA11y';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onSuggestedData: (data: any) => void;
  taskType: 'magic_fill_examination' | 'fill_biomarker_form' | 'fill_medication_form' | 'chat';
  context?: Record<string, any>;
  title?: string;
  subtitle?: string;
  description?: string;
  placeholder?: string;
}

export const AIMagicFillModal: React.FC<Props> = ({ 
  isOpen, 
  onClose, 
  onSuggestedData,
  taskType,
  context = {},
  title = "Magic Fill",
  subtitle = "AI-Powered Data Extraction",
  description = "Describe the details in natural language. Our AI will automatically extract the relevant information to populate the form fields for you.",
  placeholder = "e.g. Describe the visit or data here..."
}) => {
  const [userInput, setUserInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useModalA11y(isOpen, onClose);

  if (!isOpen) return null;

  const handleProcess = async () => {
    if (!userInput.trim()) return;
    
    setLoading(true);
    setError(null);
    try {
      const response = await getAIAssistance({
        task_type: taskType,
        user_input: userInput,
        context
      });

      if (response.success && response.suggested_data) {
        onSuggestedData(response.suggested_data);
        onClose();
        setUserInput('');
      } else {
        setError(response.error || "AI could not process your request.");
      }
    } catch (err: any) {
      console.error("AI Magic Fill Error:", err);
      setError(err.response?.data?.detail || "Assistant is currently unavailable.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[1000] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-in fade-in duration-300">
      <div role="dialog" aria-modal="true" className="bg-white dark:bg-dark-surface w-full max-w-2xl rounded-[2.5rem] shadow-2xl border border-gray-100 dark:border-dark-border overflow-hidden flex flex-col animate-in zoom-in-95 duration-300">
        {/* Header */}
        <div className="px-10 py-8 border-b border-gray-50 dark:border-dark-border flex items-center justify-between bg-gradient-to-r from-indigo-50/50 to-blue-50/50 dark:from-indigo-900/10 dark:to-blue-900/10">
          <div className="flex items-center space-x-4">
            <div className="p-3 bg-indigo-600 text-white rounded-2xl shadow-xl shadow-indigo-200 dark:shadow-none">
              <Sparkles className="w-6 h-6" />
            </div>
            <div>
              <div className="flex items-center gap-2 flex-wrap">
                <h2 className="text-2xl font-black text-brand-navy dark:text-dark-text tracking-tight uppercase">{title}</h2>
                <AIBadge taskType={taskType} />
              </div>
              <p className="text-[11px] text-indigo-600 dark:text-indigo-400 font-black uppercase tracking-[0.2em] mt-1">{subtitle}</p>
            </div>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors">
            <X className="w-6 h-6 text-gray-300" />
          </button>
        </div>

        <div className="p-10">
          <div className="mb-8">
            <label className="block text-[11px] font-black text-gray-400 uppercase tracking-[0.2em] mb-4 px-1">Describe Details</label>
            <textarea
              autoFocus
              rows={6}
              value={userInput}
              onChange={(e) => setUserInput(e.target.value)}
              placeholder={placeholder}
              className="w-full px-8 py-6 bg-gray-50 dark:bg-dark-bg border-none rounded-[2rem] text-sm text-gray-900 dark:text-dark-text placeholder-gray-400 focus:ring-2 focus:ring-indigo-500/20 transition-all shadow-inner outline-none resize-none leading-relaxed"
            />
          </div>

          <div className="bg-blue-50 dark:bg-blue-900/10 border border-blue-100 dark:border-blue-800/30 p-6 rounded-3xl flex items-start gap-4">
            <div className="p-2 bg-white dark:bg-dark-surface rounded-xl shadow-sm">
              <Wand2 className="w-4 h-4 text-blue-600 dark:text-blue-400" />
            </div>
            <p className="text-xs text-blue-700 dark:text-blue-300 font-medium leading-relaxed">
              {description}
            </p>
          </div>

          {error && (
            <div className="mt-6 p-4 bg-red-50 dark:bg-red-900/10 border border-red-100 dark:border-red-900/20 rounded-2xl text-red-600 dark:text-red-400 text-xs font-bold text-center">
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-10 py-8 bg-gray-50 dark:bg-dark-bg/50 border-t border-gray-50 dark:border-dark-border flex items-center justify-end space-x-6">
          <button
            type="button"
            onClick={onClose}
            className="px-6 py-2.5 text-xs font-black text-gray-400 hover:text-gray-600 transition-colors uppercase tracking-[0.2em]"
          >
            Cancel
          </button>
          <button
            onClick={handleProcess}
            disabled={loading || !userInput.trim()}
            className="px-12 py-4 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:shadow-none text-white rounded-[1.2rem] font-black text-xs shadow-2xl shadow-indigo-200 dark:shadow-none transition-all flex items-center space-x-3 uppercase tracking-[0.15em] active:scale-95"
          >
            {loading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Sparkles className="w-4 h-4" />
            )}
            <span>{loading ? 'Processing...' : `Apply ${title}`}</span>
          </button>
        </div>
      </div>
    </div>
  );
};
