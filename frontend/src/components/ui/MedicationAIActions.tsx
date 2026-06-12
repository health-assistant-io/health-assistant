import React from 'react';
import { Pill, AlertTriangle, ShieldCheck } from 'lucide-react';
import { AIActionsDropdown, AIAction } from './AIActionsDropdown';
import { useTranslation } from 'react-i18next';

interface Props {
  medicationId: string;
  medicationName?: string;
  className?: string;
  align?: 'left' | 'right';
}

export const MedicationAIActions: React.FC<Props> = ({ 
  medicationId,
  medicationName,
  className = "",
  align = 'right'
}) => {
  const { t } = useTranslation();
  const name = medicationName || t('patients.unknown');

  const actions: AIAction[] = [
    {
      label: t('medications.ai_actions.explain.label'),
      description: t('medications.ai_actions.explain.description'),
      icon: Pill,
      prompt: t('medications.ai_actions.explain.prompt', { name }),
      color: "bg-indigo-50 dark:bg-indigo-900/40 text-indigo-600"
    },
    {
      label: t('medications.ai_actions.interactions.label'),
      description: t('medications.ai_actions.interactions.description'),
      icon: ShieldCheck,
      prompt: t('medications.ai_actions.interactions.prompt', { name }),
      color: "bg-emerald-50 dark:bg-emerald-900/40 text-emerald-600"
    },
    {
      label: t('medications.ai_actions.side_effects.label'),
      description: t('medications.ai_actions.side_effects.description'),
      icon: AlertTriangle,
      prompt: t('medications.ai_actions.side_effects.prompt', { name }),
      color: "bg-red-50 dark:bg-red-900/40 text-red-600"
    },
    {
      label: t('medications.ai_actions.dosage.label'),
      description: t('medications.ai_actions.dosage.description'),
      icon: Pill,
      prompt: t('medications.ai_actions.dosage.prompt', { name }),
      color: "bg-blue-50 dark:bg-blue-900/40 text-blue-600"
    }
  ];

  return (
    <AIActionsDropdown
      actions={actions}
      contextId={medicationId}
      contextType="medication"
      title={t('medications.ai_actions_title')}
      className={className}
      align={align}
    />
  );
};
