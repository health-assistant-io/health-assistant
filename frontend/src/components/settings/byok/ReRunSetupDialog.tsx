import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Check,
  Database,
  Eye,
  Loader2,
  Mic,
  Type,
  Volume2,
  Wrench,
} from 'lucide-react';
import {
  aiConfigApi,
  type ProviderSetupErrorDetail,
} from '../../../api/aiConfig';
import { useAIConfigStore } from '../../../store/slices/aiConfigSlice';
import type { AIProvider } from '../../../api/aiConfig';
import { Button } from '../../ui/Button';
import { Modal } from '../../ui/Modal';

import { ProviderLogo } from './ProviderLogo';
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

function SwitchRow({
  label,
  current,
  checked,
  onToggle,
}: {
  label: string;
  current: string;
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={onToggle}
      className="flex w-full items-center justify-between gap-3 rounded-lg border border-gray-200 px-3 py-2 text-left text-sm hover:bg-gray-50 dark:border-dark-border dark:bg-dark-surface dark:text-dark-text dark:hover:bg-dark-bg"
    >
      <span className="flex min-w-0 flex-col">
        <span>{label}</span>
        <span className="truncate text-xs text-gray-500 dark:text-dark-muted">{current}</span>
      </span>
      <span
        aria-hidden="true"
        className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${
          checked ? 'bg-blue-600' : 'bg-gray-200 dark:bg-dark-border'
        }`}
      >
        <span
          className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${
            checked ? 'left-[1.125rem]' : 'left-0.5'
          }`}
        />
      </span>
    </button>
  );
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

  const [selected, setSelected] = useState<string[]>([]);
  const [fillText, setFillText] = useState(true);
  const [fillVision, setFillVision] = useState(true);
  const [fillStt, setFillStt] = useState(true);
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
      setSelected(preset.curated_models);
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

  const apply = async () => {
    setPending(true);
    setError(null);
    setPlainError(null);
    try {
      await setupProvider(presetKey, {
        api_key: null,
        scope: scope === 'global' ? 'SYSTEM' : scope === 'tenant' ? 'TENANT' : 'USER',
        options: {
          curated_ids: preset?.curated_models ? selected : undefined,
          bind_chat: fillText,
          bind_vision: fillVision,
          bind_stt: fillStt,
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
        {preset && preset.curated_models && preset.curated_models.length > 0 ? (
          <div
            role="group"
            aria-label={t('settings.ai.setup.review.models_label')}
            className="grid grid-cols-1 gap-2 sm:grid-cols-2"
          >
            {preset.curated_models.map((modelId) => {
              const isSelected = selected.includes(modelId);
              return (
                <button
                  type="button"
                  key={modelId}
                  aria-pressed={isSelected}
                  onClick={() =>
                    setSelected((current) =>
                      isSelected
                        ? current.filter((id) => id !== modelId)
                        : [...current, modelId],
                    )
                  }
                  className={`flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-left text-sm ${
                    isSelected
                      ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/30'
                      : 'border-gray-200 bg-white opacity-70 hover:bg-gray-50 dark:border-dark-border dark:bg-dark-surface dark:hover:bg-dark-bg'
                  }`}
                >
                  <span className="flex min-w-0 flex-col gap-1">
                    <span className="flex items-center gap-2 truncate font-medium text-gray-900 dark:text-dark-text">
                      <ProviderLogo presetKey={presetKey} label={modelId} />
                      {modelId}
                    </span>
                    <span className="flex items-center gap-2 text-xs text-gray-500 dark:text-dark-muted">
                      {guessCaps(modelId).map((cap) => {
                        const Icon = CAP_ICONS[cap];
                        return Icon ? <Icon key={cap} className="h-3.5 w-3.5" aria-hidden /> : null;
                      })}
                    </span>
                  </span>
                  {isSelected ? (
                    <Check className="h-4 w-4 shrink-0 text-blue-600 dark:text-blue-400" aria-hidden />
                  ) : null}
                </button>
              );
            })}
          </div>
        ) : null}
        <div className="space-y-2">
          <SwitchRow
            label={t('settings.ai.setup.review.fill_text')}
            current={nowLabel('default')}
            checked={fillText}
            onToggle={() => setFillText((v) => !v)}
          />
          <SwitchRow
            label={t('settings.ai.setup.review.fill_vision')}
            current={nowLabel('ocr')}
            checked={fillVision}
            onToggle={() => setFillVision((v) => !v)}
          />
          {preset?.stt_model ? (
            <SwitchRow
              label={t('settings.ai.setup.review.fill_stt')}
              current={nowLabel('transcription')}
              checked={fillStt}
              onToggle={() => setFillStt((v) => !v)}
            />
          ) : null}
        </div>
        <p className="text-xs text-gray-500 dark:text-dark-muted">
          {t('settings.ai.setup.review.footnote')}
        </p>
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
