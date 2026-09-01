import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { AudioLines, Cpu, type LucideIcon } from 'lucide-react';
import {
  TaskAssignmentPicker,
  type ModelPickerProvider,
  type TaskAssignmentSection,
} from '@neuronection/assistant-ui';
import { useAIConfigStore } from '../../store/slices/aiConfigSlice';

interface TaskTypeDef {
  value: string;
  labelKey: string;
  /** Capability the backend requires for this task
   *  (app/ai/providers/capabilities.py; unmapped tasks default to text). */
  requires: 'text' | 'vision' | 'audio_input';
  icon: LucideIcon;
}

const TASK_TYPES: TaskTypeDef[] = [
  { value: 'ocr', labelKey: 'settings.ai.task_ocr', requires: 'vision', icon: Cpu },
  { value: 'nlp', labelKey: 'settings.ai.task_nlp', requires: 'text', icon: Cpu },
  {
    value: 'medication_interaction',
    labelKey: 'settings.ai.task_medication_interaction',
    requires: 'text',
    icon: Cpu,
  },
  {
    value: 'anomaly_detection',
    labelKey: 'settings.ai.task_anomaly_detection',
    requires: 'text',
    icon: Cpu,
  },
  {
    value: 'fill_biomarker_form',
    labelKey: 'settings.ai.task_fill_biomarker_form',
    requires: 'text',
    icon: Cpu,
  },
  {
    value: 'fill_medication_form',
    labelKey: 'settings.ai.task_fill_medication_form',
    requires: 'text',
    icon: Cpu,
  },
  {
    value: 'magic_fill_examination',
    labelKey: 'settings.ai.task_magic_fill_examination',
    requires: 'text',
    icon: Cpu,
  },
  {
    value: 'define_biomarker',
    labelKey: 'settings.ai.task_define_biomarker',
    requires: 'text',
    icon: Cpu,
  },
  {
    value: 'define_medication',
    labelKey: 'settings.ai.task_define_medication',
    requires: 'text',
    icon: Cpu,
  },
  {
    value: 'suggest_category_icon',
    labelKey: 'settings.ai.task_suggest_category_icon',
    requires: 'text',
    icon: Cpu,
  },
  {
    value: 'generate_category_icon',
    labelKey: 'settings.ai.task_generate_category_icon',
    requires: 'text',
    icon: Cpu,
  },
  { value: 'chat', labelKey: 'settings.ai.task_chat', requires: 'text', icon: Cpu },
  {
    value: 'transcription',
    labelKey: 'settings.ai.task_transcription',
    requires: 'audio_input',
    icon: AudioLines,
  },
];

const DEFAULT_TASK_TYPE = 'default';

interface TaskAssignmentProps {
  scope?: 'global' | 'tenant' | 'user';
  userId?: string;
  tenantId?: string;
}

export const TaskAssignment: React.FC<TaskAssignmentProps> = ({
  scope = 'user',
  userId,
  tenantId,
}) => {
  const { t } = useTranslation();
  const {
    providers,
    models,
    taskAssignments,
    createTaskAssignment,
    updateTaskAssignment,
    deleteTaskAssignment,
    isLoading,
    error,
    clearError,
  } = useAIConfigStore();

  const assignmentFor = (taskType: string) =>
    taskAssignments.find((a) => a.task_type === taskType && a.is_active);

  const catalog: ModelPickerProvider[] = useMemo(() => {
    const groups: Record<string, ModelPickerProvider> = {};
    for (const model of models) {
      if (!model.is_active) continue;
      groups[model.provider_id] = groups[model.provider_id] ?? {
        id: model.provider_id,
        name: providers.find((p) => p.id === model.provider_id)?.name ?? `#${model.provider_id}`,
        models: [],
      };
      groups[model.provider_id].models.push({
        id: model.id,
        name: model.name || model.model_name,
        capabilities: model.capabilities?.length ? model.capabilities : ['text'],
      });
    }
    return Object.values(groups);
  }, [providers, models]);

  const value: Record<string, string | null> = {};
  const secondaryValue: Record<string, string | null> = {};
  for (const task of TASK_TYPES) {
    value[task.value] = assignmentFor(task.value)?.model_id ?? null;
  }
  secondaryValue[DEFAULT_TASK_TYPE] =
    assignmentFor(DEFAULT_TASK_TYPE)?.model_id ?? null;

  const assign = async (taskType: string, modelId: string | null) => {
    const existing = assignmentFor(taskType);
    if (!modelId) {
      if (existing) {
        await deleteTaskAssignment(existing.id);
      }
      return;
    }
    const providerId = models.find((m) => m.id === modelId)?.provider_id;
    const apiScope =
      scope === 'global' ? 'SYSTEM' : scope === 'tenant' ? 'TENANT' : 'USER';
    if (existing) {
      await updateTaskAssignment(existing.id, {
        provider_id: providerId,
        model_id: modelId,
        is_active: true,
      });
    } else {
      await createTaskAssignment({
        task_type: taskType,
        scope: apiScope,
        provider_id: providerId,
        model_id: modelId,
        is_active: true,
        priority: 0,
        ...(scope === 'user' && userId ? { user_id: userId } : {}),
        ...(scope === 'tenant' && tenantId ? { tenant_id: tenantId } : {}),
      });
    }
  };

  const sections: TaskAssignmentSection[] = [
    {
      id: 'fallback',
      label: t('settings.ai.section_fallback'),
      description: t('settings.ai.section_fallback_hint'),
      tasks: [
        {
          id: DEFAULT_TASK_TYPE,
          label: t('settings.ai.task_default'),
          requires: 'text',
          icon: Cpu,
          secondaryOnly: true,
        },
      ],
    },
    {
      id: 'tasks',
      label: t('settings.ai.section_tasks'),
      tasks: TASK_TYPES.map((task) => ({
        id: task.value,
        label: t(task.labelKey),
        requires: task.requires,
        icon: task.icon,
      })),
    },
  ];

  return (
    <div className="max-w-3xl space-y-4">
      <p className="text-sm text-gray-500 dark:text-dark-muted">{t('settings.ai.tasks_hint')}</p>
      {error ? (
        <div className="flex items-center justify-between rounded-lg border border-red-200 bg-red-100 p-3 dark:border-red-900/50 dark:bg-red-900/30">
          <span className="text-sm text-red-700 dark:text-red-300">{error}</span>
          <button onClick={clearError} className="px-2 py-1 text-xs font-bold underline">
            {t('settings.ai.dismiss')}
          </button>
        </div>
      ) : null}
      <TaskAssignmentPicker
        sections={sections}
        providers={catalog}
        value={value}
        secondaryValue={secondaryValue}
        onAssign={(taskId, modelId) =>
          void assign(taskId, modelId || null).catch(() => undefined)
        }
        onAssignSecondary={(taskId, modelId) =>
          void assign(taskId, modelId || null).catch(() => undefined)
        }
        secondaryLabel={t('settings.ai.fallback_label')}
        primaryLabel={t('settings.ai.primary_label')}
        primaryInfo={t('settings.ai.primary_info')}
        fallbackInfo={t('settings.ai.fallback_info')}
        clearLabel={t('settings.ai.clear_assignment')}
        disabled={isLoading}
        renderMeta={(task) => {
          if (task.id === DEFAULT_TASK_TYPE) return null;
          const assigned = assignmentFor(task.id);
          if (assigned) return null;
          return (
            <p className="text-xs italic text-gray-500 dark:text-gray-400">
              {t('settings.ai.not_assigned_hint', {
                scope:
                  scope === 'user'
                    ? t('settings.ai.scope_org')
                    : t('settings.ai.scope_system'),
              })}
            </p>
          );
        }}
      />
    </div>
  );
};
