import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AudioLines,
  Cpu,
  Eye,
  Languages,
  MessageSquare,
  ShieldCheck,
  Sparkles,
  Tag,
  Wrench,
  type LucideIcon,
} from 'lucide-react';
import {
  TaskAssignmentPicker,
  type ModelPickerProvider,
  type TaskAssignmentSection,
} from '@neuronection/assistant-ui';
import { useAIConfigStore } from '../../store/slices/aiConfigSlice';
import { isDecisionSlot } from '../../api/aiConfig';
import { Button } from '../ui/Button';
import { ProviderSetupModal } from './byok/ProviderSetupModal';

const DEFAULT_SLOT_TASKS = [
  { value: 'default', labelKey: 'settings.ai.task_default', requires: 'text' as const, icon: MessageSquare, secondaryOnly: true },
  { value: 'ocr', labelKey: 'settings.ai.task_ocr', requires: 'vision' as const, icon: Eye },
  {
    value: 'transcription',
    labelKey: 'settings.ai.task_transcription',
    requires: 'stt' as const,
    icon: AudioLines,
  },
];

/** Decision slots (plan 26 / ADR-0032): health task types for the family
 *  `decision:<cap>` slots; fallback = priority ordering (§3 contract table). */
const DECISION_SLOT_TASKS = [
  {
    value: 'decision_tool_dispatch',
    labelKey: 'settings.ai.slot_decision_tool_dispatch',
    hintKey: 'settings.ai.slot_decision_tool_dispatch_hint',
    icon: Wrench,
  },
  {
    value: 'decision_boolean_gate',
    labelKey: 'settings.ai.slot_decision_boolean_gate',
    hintKey: 'settings.ai.slot_decision_boolean_gate_hint',
    icon: ShieldCheck,
  },
  {
    value: 'decision_choice',
    labelKey: 'settings.ai.slot_decision_choice',
    hintKey: 'settings.ai.slot_decision_choice_hint',
    icon: Languages,
  },
  {
    value: 'decision_score',
    labelKey: 'settings.ai.slot_decision_score',
    hintKey: 'settings.ai.slot_decision_score_hint',
    icon: Tag,
  },
];

const OTHER_TASKS = [
  { value: 'nlp', labelKey: 'settings.ai.task_nlp', requires: 'text' as const, icon: Cpu },
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
    configSummary,
    createTaskAssignment,
    updateTaskAssignment,
    deleteTaskAssignment,
    isLoading,
    error,
    clearError,
  } = useAIConfigStore();
  const [setupOpen, setSetupOpen] = useState(false);

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
  for (const task of [...DEFAULT_SLOT_TASKS, ...OTHER_TASKS, ...DECISION_SLOT_TASKS]) {
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

  // §15 defaults-first IA (desktop parity): the modality slots (text /
  // vision / stt) lead, the Decision models group follows (D3/D12 — the
  // verbatim privacy notice ships as its description, per §13.5), derived
  // tasks last.
  const sections: TaskAssignmentSection[] = [
    {
      id: 'defaults',
      label: t('settings.ai.section_defaults'),
      description: t('settings.ai.section_defaults_hint'),
      tasks: DEFAULT_SLOT_TASKS.map((task) => ({
        id: task.value,
        label: t(task.labelKey),
        requires: task.requires,
        icon: task.icon,
        ...(task.secondaryOnly ? { secondaryOnly: true } : {}),
      })),
    },
    {
      id: 'decision',
      label: t('settings.ai.section_decision'),
      description: t('settings.ai.decision_privacy'),
      tasks: DECISION_SLOT_TASKS.map((task) => ({
        id: task.value,
        label: t(task.labelKey),
        description: t(task.hintKey),
        requires: 'decision' as const,
        icon: task.icon,
      })),
    },
    {
      id: 'other',
      label: t('settings.ai.section_tasks'),
      tasks: OTHER_TASKS.map((task) => ({
        id: task.value,
        label: t(task.labelKey),
        requires: task.requires,
        icon: task.icon,
      })),
    },
  ];

  const togglePhi = async (taskType: string, phiAllowed: boolean) => {
    const existing = assignmentFor(taskType);
    if (!existing) return;
    await updateTaskAssignment(existing.id, { phi_allowed: phiAllowed });
  };

  return (
    <div className="max-w-3xl space-y-4">
      <p className="text-sm text-gray-500 dark:text-dark-muted">{t('settings.ai.tasks_hint')}</p>
      {!configSummary?.default?.model ? (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 dark:border-amber-900/50 dark:bg-amber-900/20">
          <span className="text-sm text-amber-700 dark:text-amber-400">
            {t('settings.ai.setup.no_model_deep_link')}
          </span>
          <Button size="sm" variant="ghost" onClick={() => setSetupOpen(true)}>
            <Sparkles aria-hidden />
            {t('settings.ai.setup.open')}
          </Button>
        </div>
      ) : null}
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
          const assigned = assignmentFor(task.id);
          if (isDecisionSlot(task.id) && assigned) {
            // D17 PHI scope guard: the explicit opt-in lives on the
            // decision assignment itself; default off, surfaced here.
            return (
              <label className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
                <input
                  type="checkbox"
                  checked={assigned.phi_allowed ?? false}
                  onChange={(e) =>
                    void togglePhi(task.id, e.target.checked).catch(() => undefined)
                  }
                />
                {t('settings.ai.phi_override')}
              </label>
            );
          }
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
      <ProviderSetupModal
        open={setupOpen}
        onClose={() => setSetupOpen(false)}
        onManual={() => setSetupOpen(false)}
      />
    </div>
  );
};
