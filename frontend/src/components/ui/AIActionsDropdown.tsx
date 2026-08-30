import {
  AiActionsDropdown as LibraryAiActionsDropdown,
  type AiAction,
} from '@neuronection/assistant-ui';
import { useUIStore } from '../../store/slices/uiSlice';

export interface AIAction {
  label: string;
  description?: string;
  icon: AiAction['icon'];
  prompt: string;
  color?: string;
}

interface Props {
  actions: AIAction[];
  contextId: string;
  contextType: 'examination' | 'biomarker' | 'medication' | 'allergy';
  title?: string;
  className?: string;
  align?: 'left' | 'right';
}

/**
 * App-side glue over the library `AiActionsDropdown`: every prompt (preset
 * action or custom question) routes through the AI drawer with this
 * record's context set in the ui store.
 */
export const AIActionsDropdown: React.FC<Props> = ({
  actions,
  contextId,
  contextType,
  title = 'AI Actions',
  className,
  align = 'right',
}) => {
  const setPendingAIMessage = useUIStore((state) => state.setPendingAIMessage);
  const setAIDrawerOpen = useUIStore((state) => state.setAIDrawerOpen);
  const setCurrentExaminationId = useUIStore((state) => state.setCurrentExaminationId);
  const setCurrentBiomarkerId = useUIStore((state) => state.setCurrentBiomarkerId);
  const setCurrentMedicationId = useUIStore((state) => state.setCurrentMedicationId);
  const setCurrentAllergyId = useUIStore((state) => state.setCurrentAllergyId);

  const libraryActions: AiAction[] = actions.map((action, index) => ({
    id: `${index}-${action.prompt}`,
    label: action.label,
    description: action.description,
    icon: action.icon,
  }));
  const promptById = new Map(libraryActions.map((libAction, index) => [libAction.id, actions[index]!.prompt]));

  const runPrompt = (prompt: string) => {
    if (contextType === 'examination') setCurrentExaminationId(contextId);
    if (contextType === 'biomarker') setCurrentBiomarkerId(contextId);
    if (contextType === 'medication') setCurrentMedicationId(contextId);
    if (contextType === 'allergy') setCurrentAllergyId(contextId);
    setPendingAIMessage(prompt);
    setAIDrawerOpen(true);
  };

  return (
    <LibraryAiActionsDropdown
      actions={libraryActions}
      onAction={(action) => runPrompt(promptById.get(action.id) ?? action.id)}
      onPrompt={runPrompt}
      title={title}
      promptLabel="Ask a specific question…"
      promptPlaceholder={`e.g. Tell me more about this ${contextType}…`}
      align={align === 'left' ? 'start' : 'end'}
      className={className}
    />
  );
};
