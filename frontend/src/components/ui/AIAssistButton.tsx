import React, { useState } from 'react';
import { getAIAssistance } from '../../services/aiAssistanceService';
import { AiButton } from '@neuronection/assistant-ui';

interface Props {
  taskType: 'fill_biomarker_form' | 'fill_medication_form' | 'define_biomarker' | 'define_medication' | 'chat' | 'magic_fill_examination';
  context: Record<string, any>;
  onSuggestedData: (data: any) => void;
  className?: string;
  placeholder?: string;
  showLabel?: boolean;
}

const DEFAULT_PLACEHOLDERS: Record<Props['taskType'], string> = {
  define_biomarker: "Enter biomarker name or details (e.g. 'Creatinine definition')",
  define_medication: "Enter medication name or details (e.g. 'Ibuprofen definition')",
  fill_medication_form: 'Describe medication (e.g. ‘Metformin 500mg twice daily’)',
  fill_biomarker_form: 'Describe data (e.g. ‘Blood sugar 110 mg/dL normal’)',
  magic_fill_examination: 'Describe the examination (e.g. ‘Blood test for glucose’)',
  chat: 'Ask the AI assistant…',
};

/**
 * Form-fill affordance over the library `AiButton`: the prompt goes to the
 * assistance service; suggested data is applied by the caller and the
 * panel auto-closes (ADR-006 — the service call stays app-side).
 */
export const AIAssistButton: React.FC<Props> = ({
  taskType,
  context,
  onSuggestedData,
  className = '',
  placeholder,
  showLabel = true,
}) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const ask = async (userInput: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await getAIAssistance({
        task_type: taskType,
        user_input: userInput,
        context,
      });
      if (response.success && response.suggested_data) {
        onSuggestedData(response.suggested_data);
        setOpen(false);
      } else {
        setError(response.error || 'AI could not process your request.');
      }
    } catch (err: any) {
      console.error('AI Assistance Error:', err);
      setError(err.response?.data?.detail || 'Assistant is currently unavailable.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <AiButton
      className={className}
      label="AI Assist"
      promptLabel="Describe the data"
      placeholder={placeholder ?? DEFAULT_PLACEHOLDERS[taskType]}
      showLabel={showLabel}
      open={open}
      onOpenChange={setOpen}
      loading={loading}
      error={error}
      onSubmit={(prompt) => void ask(prompt)}
    />
  );
};
