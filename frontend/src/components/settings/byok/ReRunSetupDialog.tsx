import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Database, Eye, Loader2, Mic, Type, Volume2, Wrench } from 'lucide-react';
import { SetupReview, type SetupReviewOptions } from '@neuronection/assistant-ui/setup-review';
import {
  aiConfigApi,
  DECISION_DEFAULT_TASK_TYPE,
  type ProviderSetupErrorDetail,
} from '../../../api/aiConfig';
import { useAIConfigStore } from '../../../store/slices/aiConfigSlice';
import type { AIProvider } from '../../../api/aiConfig';
import { Button } from '../../ui/Button';
import { Modal } from '../../ui/Modal';

import { SetupErrorPanel, setupErrorDetail } from './setupErrors';

const CAP_ICONS: Record<string, typeof Type> = {
  text: Type,
  vision: Eye,
  tools: Wrench,
  embeddings: Database,
  stt: Mic,
  tts: Volume2,
};

/** Display-only capability guess for curated model cards (§15 review). */
function guessCaps(externalId: string): string[] {
  const id = externalId.toLowerCase();
  if (id.includes('embedding') || id.includes('bge')) return ['embeddings'];
  if (/(whisper|transcribe|stt)/.test(id)) return ['stt'];
  if (/(^tts|tts-|speech|voice)/.test(id)) return ['tts'];
  const caps = ['text'];
  if (/(4o|gpt-5|vision|vl|claude|gemini|llava|pixtral|gemma3)/.test(id)) caps.push('vision');
  if (/(gpt-4|gpt-5|o3|o4|claude|gemini|deepseek|qwen|llama-3|mistral)/.test(id))
    caps.push('tools');
  return caps;
}

/** Per-provider "Set up automatically" review dialog (§15, desktop ApiTab
 *  parity): stored-key note, curated multi-select cards, fill-empty-slot
 *  switch rows with the current slot values, never-removes footnote. */
export const ReRunSetupDialog: React.FC<{
  provider: AIProvider;
  onClose: () => void;
  scope?: 'global' | 'tenant' | 'user';
}> = ({ provider, onClose, scope = 'user' }) => {
  const { t } = useTranslation();
  const setupProvider = useAIConfigStore((state) => state.setupProvider);
  const configSummary = useAIConfigStore((state) => state.configSummary);

  const presetKey = provider.preset_key ?? '';
  const [preset, setPreset] = useState<
    (import('../../../api/aiConfig').ProviderPreset & { curated_models?: string[] | null }) | null
  >(null);

  const [options, setOptions] = useState<SetupReviewOptions>({
    curatedIds: [],
    bindChat: true,
    bindVision: true,
    bindStt: true,
    bindDecision: true,
  });
  const initialized = useRef(false);
  useEffect(() => {
    if (!presetKey) return;
    aiConfigApi
      .listProviderPresets()
      .then((data) => setPreset(data.presets[presetKey] ?? null))
      .catch(() => setPreset(null));
  }, [presetKey]);

  useEffect(() => {
    if (!initialized.current && preset?.curated_models) {
      initialized.current = true;
      setOptions((current) => ({ ...current, curatedIds: preset.curated_models ?? [] }));
    }
  }, [preset]);

  const [error, setError] = useState<ProviderSetupErrorDetail | null>(null);
  const [plainError, setPlainError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const nowLabel = (slotTask: string) => {
    const model = configSummary?.[slotTask as 'default']?.model;
    return t('settings.ai.setup.review.now', {
      model: model?.model_name ?? t('settings.ai.setup.review.unset'),
    });
  };

  const decisionDeclared = (preset?.decision_models?.length ?? 0) > 0;
  const bindDecision = decisionDeclared && options.bindDecision;

  const reviewSlots = [
    {
      id: 'chat',
      label: t('settings.ai.setup.review.fill_text'),
      bind: options.bindChat,
      currentValue: nowLabel('default'),
      optionsKey: 'bindChat' as const,
    },
    {
      id: 'vision',
      label: t('settings.ai.setup.review.fill_vision'),
      bind: options.bindVision,
      currentValue: nowLabel('ocr'),
      optionsKey: 'bindVision' as const,
    },
    ...(preset?.stt_model
      ? [
          {
            id: 'stt',
            label: t('settings.ai.setup.review.fill_stt'),
            bind: options.bindStt,
            currentValue: nowLabel('transcription'),
            optionsKey: 'bindStt' as const,
          },
        ]
      : []),
    ...(decisionDeclared
      ? [
          {
            id: 'decision',
            label: t('settings.ai.setup.review.fill_decision'),
            bind: bindDecision,
            // ADR-0036 §3: the single `decision` default slot — its
            // "now" value reads like the other rows.
            currentValue: nowLabel(DECISION_DEFAULT_TASK_TYPE),
            optionsKey: 'bindDecision' as const,
          },
        ]
      : []),
  ];

  const apply = async () => {
    setPending(true);
    setError(null);
    setPlainError(null);
    try {
      await setupProvider(presetKey, {
        api_key: null,
        scope: scope === 'global' ? 'SYSTEM' : scope === 'tenant' ? 'TENANT' : 'USER',
        options: {
          curated_ids: preset?.curated_models ? options.curatedIds : undefined,
          bind_chat: options.bindChat,
          bind_vision: options.bindVision,
          bind_stt: options.bindStt,
          bind_decision: bindDecision,
        },
      });
      onClose();
    } catch (err: any) {
      const detail = setupErrorDetail(err);
      setError(detail);
      setPlainError(detail ? null : (err?.message ?? String(err)));
    } finally {
      setPending(false);
    }
  };

  return (
    <Modal
      open
      onOpenChange={(open) => (!open ? onClose() : undefined)}
      title={t('settings.ai.setup.automatically')}
    >
      <div className="space-y-3">
        <p className="text-sm text-gray-500 dark:text-dark-muted">
          {t('settings.ai.setup.review.key_note', { name: provider.name })}
        </p>
        <SetupReview
          models={(preset?.curated_models ?? []).map((modelId) => ({
            id: modelId,
            name: modelId,
            caps: guessCaps(modelId),
            selected: options.curatedIds.includes(modelId),
          }))}
          slots={reviewSlots}
          capIcons={CAP_ICONS}
          modelsLabel={t('settings.ai.setup.review.models_label')}
          footnote={t('settings.ai.setup.review.footnote')}
          onOptionsChange={setOptions}
        />
        <SetupErrorPanel error={error} plainError={plainError} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>
            {t('settings.ai.cancel')}
          </Button>
          <Button size="sm" disabled={pending} onClick={() => void apply().catch(() => undefined)}>
            {pending ? <Loader2 className="animate-spin" aria-hidden /> : <Check aria-hidden />}
            {t('settings.ai.setup.review.apply')}
          </Button>
        </div>
      </div>
    </Modal>
  );
};
