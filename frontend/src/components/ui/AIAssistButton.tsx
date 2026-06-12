import React, { useState } from 'react';
import { Sparkles, Send, X, Loader2 } from 'lucide-react';
import { getAIAssistance } from '../../services/aiAssistanceService';

interface Props {
  taskType: 'fill_biomarker_form' | 'fill_medication_form' | 'define_biomarker' | 'define_medication' | 'chat' | 'magic_fill_examination';
  context: Record<string, any>;
  onSuggestedData: (data: any) => void;
  className?: string;
  placeholder?: string;
  showLabel?: boolean;
}

export const AIAssistButton: React.FC<Props> = ({ 
  taskType, 
  context, 
  onSuggestedData, 
  className = "",
  placeholder,
  showLabel = true
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [userInput, setUserInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const getDefaultPlaceholder = () => {
    if (placeholder) return placeholder;
    switch (taskType) {
      case 'define_biomarker':
        return "Enter biomarker name or details (e.g. 'Creatinine definition')";
      case 'define_medication':
        return "Enter medication name or details (e.g. 'Ibuprofen definition')";
      case 'fill_medication_form':
        return "Describe medication (e.g. 'Metformin 500mg twice daily')";
      case 'fill_biomarker_form':
        return "Describe data (e.g. 'Blood sugar 110 mg/dL normal')";
      case 'magic_fill_examination':
        return "Describe the examination (e.g. 'Blood test for glucose')";
      default:
        return "Ask the AI assistant...";
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
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
        setIsOpen(false);
        setUserInput('');
      } else {
        setError(response.error || "AI could not process your request.");
      }
    } catch (err: any) {
      console.error("AI Assistance Error:", err);
      setError(err.response?.data?.detail || "Assistant is currently unavailable.");
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) {
    return (
      <button
        type="button"
        onClick={() => setIsOpen(true)}
        className={`flex items-center space-x-2 p-1.5 bg-indigo-50 dark:bg-indigo-900/20 text-indigo-600 dark:text-indigo-400 rounded-lg border border-indigo-100 dark:border-indigo-900/30 hover:bg-indigo-100 dark:hover:bg-indigo-900/30 transition-all group ${className}`}
        title="Get AI Assistance"
      >
        <Sparkles className="w-4 h-4 group-hover:animate-pulse" />
        {showLabel && <span className="text-[10px] font-black uppercase tracking-widest px-1">Magic Fill</span>}
      </button>
    );
  }

  return (
    <div className={`relative ${className} animate-in zoom-in-95 duration-200`}>
      <div className="absolute top-0 right-0 z-50 w-64 bg-white dark:bg-dark-surface border border-indigo-100 dark:border-indigo-900/30 rounded-2xl shadow-2xl p-4 overflow-hidden">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center space-x-2 text-indigo-600 dark:text-indigo-400">
            <Sparkles className="w-3.5 h-3.5" />
            <span className="text-[10px] font-black uppercase tracking-widest">
              {taskType === 'define_biomarker' || taskType === 'define_medication' ? 'Definition Builder' : 'AI Assistant'}
            </span>
          </div>
          <button 
            type="button"
            onClick={() => { setIsOpen(false); setError(null); }}
            className="p-1 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full text-gray-400"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          <textarea
            autoFocus
            rows={2}
            className="w-full px-3 py-2 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-xs text-gray-900 dark:text-dark-text placeholder-gray-400 focus:ring-1 focus:ring-indigo-500/50 resize-none"
            placeholder={getDefaultPlaceholder()}
            value={userInput}
            onChange={(e) => setUserInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSubmit(e);
              }
            }}
          />
          
          {error && (
            <p className="text-[9px] font-bold text-red-500 uppercase tracking-tighter bg-red-50 dark:bg-red-900/10 p-2 rounded-lg">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading || !userInput.trim()}
            className="w-full flex items-center justify-center space-x-2 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white rounded-xl text-[10px] font-black uppercase tracking-widest transition-all shadow-lg shadow-indigo-500/20"
          >
            {loading ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <>
                <Send className="w-3.5 h-3.5" />
                <span>Process</span>
              </>
            )}
          </button>
        </form>
      </div>
    </div>
  );
};
