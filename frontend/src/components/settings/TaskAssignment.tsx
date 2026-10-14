import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AudioLines,
  Cpu,
  Database,
  Eye,
  Languages,
  MessageSquare,
  ShieldCheck,
  Sparkles,
  Tag,
  Wrench,
  X,
  type LucideIcon,
} from 'lucide-react';
import {
  ModelPicker,
  TaskAssignmentPicker,
  type ModelPickerProvider,
  type TaskAssignmentSection,
} from '@neuronection/assistant-ui';
import { useAIConfigStore } from '../../store/slices/aiConfigSlice';
import { aiConfigApi, DECISION_DEFAULT_TASK_TYPE, isDecisionSlot, type ProviderPreset } from '../../api/aiConfig';
import { Button } from '../ui/Button';
import { ProviderSetupModal } from './byok/ProviderSetupModal';

/** The uniform "Default models" rows (plan 29 uniform ruling + ADR-0037
 *  single-slot defaults): four capability defaults + the decision
 *  default, one picker each. */
const DEFAULT_SLOT_TASKS = [
  { value: 'default', labelKey: 'settings.ai.task_default', requires: 'text' as const, icon: MessageSquare },
  { value: 'ocr', labelKey: 'settings.ai.task_ocr', requires: 'vision' as const, icon: Eye },
  {
    value: 'transcription',
    labelKey: 'settings.ai.task_transcription',
    requires: 'stt' as const,
    icon: AudioLines,
  },
  {
    value: 'embed',
    labelKey: 'settings.ai.task_embed',
    requires: 'embeddings' as const,
    icon: Database,
  },
];

/** ADR-0036: the decision default row rides the "Default models"
 *  section like every capability default (single picker; the kind rows
 *  carry the PHI opt-in explicitly below). */
const DECISION_DEFAULT_ROW = {
  value: DECISION_DEFAULT_TASK_TYPE,
  labelKey: 'settings.ai.task_decision_default',
  hintKey: 'settings.ai.task_decision_default_hint',
  requires: 'decision' as const,
  icon: Sparkles,
};

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

/** One Decision kinds group row (plan 29 / ADR-0036 D4, uniform ruling):
 *  composed from the library `ModelPicker` — its option list LEADS with
 *  the labeled null entry ("Inherits — {resolved default model}") and
 *  choosing it assigns null, which *is* the inherit state. The D17 PHI
 *  opt-in stays on the assignment row. */
function DecisionKindRow({
  slot,
  label,
  description,
  icon: Icon,
  catalog,
  modelValue,
  inheritLabel,
  clearLabel,
  disabled,
  meta,
  onAssign,
}: {
  slot: string;
  label: string;
  description: string;
  icon: LucideIcon;
  catalog: ModelPickerProvider[];
  modelValue: string | null;
  inheritLabel: string;
  clearLabel: string;
  disabled: boolean;
  meta?: React.ReactNode;
  onAssign: (modelId: string | null) => void;
}) {
  return (
    <div className="flex flex-col gap-2 rounded-[var(--as-radius-lg)] border border-[var(--as-border)] bg-[var(--as-surface-raised)] p-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-[var(--as-radius)] bg-[var(--as-muted)] text-[var(--as-muted-fg)]">
          <Icon className="size-4" aria-hidden />
        </span>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-[var(--as-fg)]">{label}</p>
          <p className="truncate text-xs text-[var(--as-muted-fg)]">{description}</p>
          {meta ?? null}
        </div>
      </div>
      <div className="flex items-center gap-1.5 self-center">
        <ModelPicker
          providers={catalog}
          value={modelValue ?? ''}
          nullOption={{ label: inheritLabel }}
          onChange={(modelId: string | null) => onAssign(modelId)}
          disabled={disabled}
          clearable={false}
          label={label}
          hideLabel
          className="w-56"
        />
        {modelValue ? (
          <button
            type="button"
            aria-label={`${clearLabel} — ${label}`}
            disabled={disabled}
            onClick={() => onAssign(null)}
            className="cursor-pointer rounded-[var(--as-radius-sm)] p-1.5 text-[var(--as-muted-fg)] transition-colors hover:bg-[var(--as-muted)] hover:text-[var(--as-fg)] focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--as-focus-ring)] disabled:pointer-events-none disabled:opacity-50"
          >
            <X className="size-4" aria-hidden />
          </button>
        ) : null}
      </div>
    </div>
  );
}

const PHI_TOGGLE_KEY = 'settings.ai.phi_override';

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
  const [presets, setPresets] = useState<Record<string, ProviderPreset> | null>(null);

  useEffect(() => {
    if (presets) return;
    let cancelled = false;
    aiConfigApi
      .listProviderPresets()
      .then((data) => {
        if (!cancelled) setPresets(data.presets);
      })
      .catch(() => {
        if (!cancelled) setPresets({});
      });
    return () => {
      cancelled = true;
    };
  }, [presets]);

  /** Decision pre-suggestion (display-only, §15 embeddings-wave companion):
   *  the first preset-stamped provider whose preset declares a
   *  `decision_default` — surfaced as a hint on EMPTY decision pickers,
   *  never auto-assigned. */
  const suggestedDecisionDefault = useMemo(() => {
    if (!presets) return null;
    for (const provider of providers) {
      const preset = provider.preset_key ? presets[provider.preset_key] : undefined;
      if (preset?.decision_default) return preset.decision_default;
    }
    return null;
  }, [presets, providers]);

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

  const decisionCatalog: ModelPickerProvider[] = useMemo(
    () =>
      catalog
        .map((provider) => ({
          ...provider,
          models: provider.models.filter((model) =>
            model.capabilities?.includes('decision'),
          ),
        }))
        .filter((provider) => provider.models.length > 0),
    [catalog],
  );

  const value: Record<string, string | null> = {};
  for (const task of [
    ...DEFAULT_SLOT_TASKS,
    ...OTHER_TASKS,
    ...DECISION_SLOT_TASKS.map((entry) => ({ value: entry.value as string })),
    DECISION_DEFAULT_ROW,
  ]) {
    value[task.value] = assignmentFor(task.value)?.model_id ?? null;
  }

  // ADR-0036 §4: what a kind's labeled null option names — the `decision`
  // default row's model (client-resolved from the summary).
  // ADR-0036 §4: what a kind's labeled null option names — the `decision`
  // default row's model, client-resolved from the value map.
  const decisionDefaultId = value[DECISION_DEFAULT_TASK_TYPE] ?? null;
  const decisionDefaultName =
    decisionCatalog.flatMap((p) => p.models).find((m) => m.id === decisionDefaultId)?.name ??
    null;
  const inheritLabel = decisionDefaultName
    ? t('settings.ai.decision_inherits_label', { model: decisionDefaultName })
    : t('settings.ai.decision_inherits_empty');

  // D7 "Simplify": only when all four kinds resolve to one model and the
  // default is empty — a suggestion, never automatic.
  let simplifyModelId: string | null = null;
  if (!decisionDefaultId) {
    let resolved: string | null = null;
    let uniform = true;
    for (const task of DECISION_SLOT_TASKS) {
      const kindModel = value[task.value] ?? null;
      if (!kindModel) {
        uniform = false;
        break;
      }
      if (resolved === null) resolved = kindModel;
      else if (resolved !== kindModel) {
        uniform = false;
        break;
      }
    }
    if (uniform && resolved !== null) simplifyModelId = resolved;
  }

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
  // vision / stt / embeddings) lead, the Decision models group follows
  // (D3/D12 — the verbatim privacy notice ships as its description, per
  // §13.5), derived tasks last.
  const sections: TaskAssignmentSection[] = [
    {
      id: 'defaults',
      label: t('settings.ai.section_defaults'),
      description: t('settings.ai.section_defaults_hint'),
      tasks: [
        ...DEFAULT_SLOT_TASKS.map((task) => ({
          id: task.value,
          label: t(task.labelKey),
          requires: task.requires,
          icon: task.icon,
        })),
        // ADR-0036: the decision default row rides the uniform section
        // like every capability default (single picker per ADR-0037).
        {
          id: DECISION_DEFAULT_ROW.value,
          label: t(DECISION_DEFAULT_ROW.labelKey),
          description: t(DECISION_DEFAULT_ROW.hintKey),
          requires: DECISION_DEFAULT_ROW.requires,
          icon: DECISION_DEFAULT_ROW.icon,
        },
      ],
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
        onAssign={(taskId, modelId) =>
          void assign(taskId, modelId || null).catch(() => undefined)
        }
        primaryLabel={t('settings.ai.primary_label')}
        primaryInfo={t('settings.ai.primary_info')}
        fallbackInfo={t('settings.ai.fallback_info')}
        clearLabel={t('settings.ai.clear_assignment')}
        disabled={isLoading}
        renderMeta={(task) => {
          const assigned = assignmentFor(task.id);
          // The decision default row: D17 PHI opt-in + the empty-default
          // hint/suggestion + the D7 Simplify chip.
          if (task.id === DECISION_DEFAULT_TASK_TYPE) {
            return (
              <div className="space-y-1">
                {assigned ? (
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
                ) : null}
                {!assigned ? (
                  <div className="space-y-1">
                    <p className="text-xs italic text-gray-400 dark:text-gray-500">
                      {t('settings.ai.decision_defaults_empty_hint')}
                      {suggestedDecisionDefault
                        ? ' ' +
                          t('settings.ai.suggested_default', {
                            model: suggestedDecisionDefault,
                          })
                        : ''}
                    </p>
                    {simplifyModelId ? (
                      <button
                        type="button"
                        className="rounded-full border border-gray-200 px-2.5 py-1 text-[11px] text-gray-600 hover:bg-gray-50 dark:border-dark-border dark:text-dark-muted"
                        onClick={() =>
                          void assign(DECISION_DEFAULT_TASK_TYPE, simplifyModelId).catch(
                            () => undefined,
                          )
                        }
                      >
                        {t('settings.ai.decision_simplify_use_as_default', {
                          model:
                            decisionCatalog
                              .flatMap((p) => p.models)
                              .find((m) => m.id === simplifyModelId)?.name ?? simplifyModelId,
                        })}
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </div>
            );
          }
          if (isDecisionSlot(task.id)) return null;
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

      {/* Decision kinds group (plan 29 / ADR-0036 uniform ruling): the
          four kind rows with the labeled inherit entry; the privacy
          notice (§13.5) is the section description. */}
      <div className="flex flex-col gap-2">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-dark-muted">
            {t('settings.ai.section_decision_kinds')}
          </p>
          <p className="mt-0.5 text-xs text-gray-500 dark:text-dark-muted">
            {t('settings.ai.decision_privacy')}
          </p>
        </div>
        {DECISION_SLOT_TASKS.map((task) => {
          const kindModel = value[task.value] ?? null;
          const kindAssigned = assignmentFor(task.value);
          return (
            <DecisionKindRow
              key={task.value}
              slot={task.value}
              label={t(task.labelKey)}
              description={t(task.hintKey)}
              icon={task.icon}
              catalog={decisionCatalog}
              modelValue={kindModel}
              inheritLabel={inheritLabel}
              clearLabel={t('settings.ai.clear_assignment')}
              disabled={isLoading}
              meta={
                <div className="space-y-1">
                  {kindAssigned ? (
                    // D17 PHI scope guard: the explicit opt-in lives on
                    // the decision assignment itself.
                    <label className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
                      <input
                        type="checkbox"
                        checked={kindAssigned.phi_allowed ?? false}
                        onChange={(e) =>
                          void togglePhi(task.value, e.target.checked).catch(() => undefined)
                        }
                      />
                      {t(PHI_TOGGLE_KEY)}
                    </label>
                  ) : null}
                  {!kindModel && suggestedDecisionDefault && !decisionDefaultId ? (
                    <p
                      className="text-xs italic text-gray-400 dark:text-gray-500"
                      data-testid="decision-suggestion"
                    >
                      {t('settings.ai.suggested_default', { model: suggestedDecisionDefault })}
                    </p>
                  ) : null}
                </div>
              }
              onAssign={(modelId) =>
                void assign(task.value, modelId).catch(() => undefined)
              }
            />
          );
        })}
      </div>
      <ProviderSetupModal
        open={setupOpen}
        onClose={() => setSetupOpen(false)}
        onManual={() => setSetupOpen(false)}
      />
    </div>
  );
};
