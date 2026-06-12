import React from 'react';
import { FileText, Info } from 'lucide-react';
import { AIActionsDropdown, AIAction } from './AIActionsDropdown';
import { useTranslation } from 'react-i18next';

interface Props {
  examinationId: string;
  className?: string;
  align?: 'left' | 'right';
}

export const ExaminationAIActions: React.FC<Props> = ({ 
  examinationId, 
  className = "",
  align = 'right'
}) => {
  const { t } = useTranslation();

  const actions: AIAction[] = [
    {
      label: t('examination_detail.ai_actions.summarize.label'),
      description: t('examination_detail.ai_actions.summarize.description'),
      icon: FileText,
      prompt: t('examination_detail.ai_actions.summarize.prompt'),
      color: "bg-blue-50 dark:bg-blue-900/40 text-blue-600"
    },
    {
      label: t('examination_detail.ai_actions.findings.label'),
      description: t('examination_detail.ai_actions.findings.description'),
      icon: Info,
      prompt: t('examination_detail.ai_actions.findings.prompt'),
      color: "bg-emerald-50 dark:bg-emerald-900/40 text-emerald-600"
    },
    {
      label: t('examination_detail.ai_actions.analysis.label'),
      description: t('examination_detail.ai_actions.analysis.description'),
      icon: Info,
      prompt: t('examination_detail.ai_actions.analysis.prompt'),
      color: "bg-amber-50 dark:bg-amber-900/40 text-amber-600"
    }
  ];

  return (
    <AIActionsDropdown
      actions={actions}
      contextId={examinationId}
      contextType="examination"
      title={t('examination_detail.header.ai_actions_title')}
      className={className}
      align={align}
    />
  );
};
