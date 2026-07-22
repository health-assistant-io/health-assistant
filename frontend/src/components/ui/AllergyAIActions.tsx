import React from 'react';
import { ShieldAlert, AlertTriangle, Activity, BookOpen } from 'lucide-react';
import { AIActionsDropdown, AIAction } from './AIActionsDropdown';
import { useTranslation } from 'react-i18next';

interface Props {
  allergyId: string;
  allergyName?: string;
  className?: string;
  align?: 'left' | 'right';
}

/**
 * AI actions dropdown for an allergen catalog entry. Mirrors
 * `MedicationAIActions`: surfaces 4 prompts that send the user to the AI
 * assistant with a prefilled, allergen-specific question.
 */
export const AllergyAIActions: React.FC<Props> = ({
  allergyId,
  allergyName,
  className = '',
  align = 'right',
}) => {
  const { t } = useTranslation();
  const name = allergyName || t('patients.unknown');

  const actions: AIAction[] = [
    {
      label: t('allergies.ai_actions.explain.label', 'Explain'),
      description: t('allergies.ai_actions.explain.description', 'What is this allergen?'),
      icon: ShieldAlert,
      prompt: t('allergies.ai_actions.explain.prompt', {
        defaultValue: `Explain the allergen "{{name}}": what it is, common sources, and how exposure typically happens.`,
        name,
      }),
      color: 'bg-rose-50 dark:bg-rose-900/40 text-rose-600',
    },
    {
      label: t('allergies.ai_actions.reactions.label', 'Reactions'),
      description: t('allergies.ai_actions.reactions.description', 'Typical reaction symptoms'),
      icon: AlertTriangle,
      prompt: t('allergies.ai_actions.reactions.prompt', {
        defaultValue:
          'What are the typical reaction symptoms for "{{name}}" allergy, and which signs would indicate a severe or anaphylactic response?',
        name,
      }),
      color: 'bg-amber-50 dark:bg-amber-900/40 text-amber-600',
    },
    {
      label: t('allergies.ai_actions.alternatives.label', 'Alternatives'),
      description: t('allergies.ai_actions.alternatives.description', 'Safe substitutes / avoidance'),
      icon: BookOpen,
      prompt: t('allergies.ai_actions.alternatives.prompt', {
        defaultValue:
          'Suggest safe alternatives and practical avoidance strategies for a patient allergic to "{{name}}".',
        name,
      }),
      color: 'bg-emerald-50 dark:bg-emerald-900/40 text-emerald-600',
    },
    {
      label: t('allergies.ai_actions.severity.label', 'Severity'),
      description: t('allergies.ai_actions.severity.description', 'Severity & management'),
      icon: Activity,
      prompt: t('allergies.ai_actions.severity.prompt', {
        defaultValue:
          'How is the severity of a "{{name}}" allergy typically assessed, and what monitoring or management plan would you recommend?',
        name,
      }),
      color: 'bg-blue-50 dark:bg-blue-900/40 text-blue-600',
    },
  ];

  return (
    <AIActionsDropdown
      actions={actions}
      contextId={allergyId}
      contextType="allergy"
      title={t('allergies.ai_actions_title', 'AI actions')}
      className={className}
      align={align}
    />
  );
};
