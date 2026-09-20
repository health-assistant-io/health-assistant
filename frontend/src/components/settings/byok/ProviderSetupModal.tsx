import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Banknote,
  Check,
  Copy,
  ExternalLink,
  House,
  Loader2,
  Settings2,
  X,
} from 'lucide-react';
import { ModelPicker, type ModelPickerProvider } from '@neuronection/assistant-ui';
import {
  aiConfigApi,
  setupErrorDetail,
  type ProviderPreset,
  type ProviderSetupErrorDetail,
  type ProviderSetupResult,
} from '../../../api/aiConfig';
import { useAIConfigStore } from '../../../store/slices/aiConfigSlice';
import { Button } from '../../ui/Button';
import { Modal } from '../../ui/Modal';
import { COUNTRIES } from '../../../utils/countryUtils';

import { ProviderLogo } from './ProviderLogo';
import { SetupErrorPanel } from './setupErrors';

type Phase = 'tiles' | 'form' | 'done';

function HostingToggle({
  local,
  onChange,
  disabled,
}: {
  local: boolean;
  onChange: (local: boolean) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-1">
      <span className="block text-sm text-gray-500 dark:text-dark-muted">
        {t('settings.ai.hosting')}
      </span>
      <div className="grid grid-cols-2 gap-2" role="group" aria-label={t('settings.ai.hosting')}>
        {(
          [
            [true, t('settings.ai.local_kind'), House],
            [false, t('settings.ai.cloud_kind'), Banknote],
          ] as const
        ).map(([value, label, Icon]) => (
          <button
            key={label}
            type="button"
            aria-pressed={local === value}
            disabled={disabled}
            onClick={() => onChange(value)}
            className={`flex items-center justify-center gap-2 rounded-lg border px-3 py-2 text-sm transition ${
              local === value
                ? 'border-blue-500 bg-blue-50 text-blue-600 dark:border-blue-500 dark:bg-blue-900/30 dark:text-blue-400'
                : 'border-gray-200 bg-white text-gray-500 hover:bg-gray-50 dark:border-dark-border dark:bg-dark-surface dark:text-dark-muted dark:hover:bg-dark-bg'
            }`}
          >
            <Icon className="h-4 w-4" aria-hidden />
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}

function CountrySelect({ country, onChange }: { country: string; onChange: (c: string) => void }) {
  const { t } = useTranslation();
  return (
    <label className="block space-y-1 text-sm">
      <span className="text-gray-500 dark:text-dark-muted">{t('settings.ai.country_label')}</span>
      <select
        className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 dark:border-dark-border dark:bg-dark-surface dark:text-dark-text"
        aria-label={t('settings.ai.country_label')}
        value={country}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">{t('settings.ai.country_placeholder')}</option>
        {COUNTRIES.map((entry) => (
          <option key={entry.code} value={entry.code}>
            {entry.flag} {entry.name}
          </option>
        ))}
      </select>
    </label>
  );
}

/** Per-slot rebind pickers shown after a successful setup (§15 success panel).
 *  Slot mapping is health's: chat→default, vision→ocr, stt→transcription. */
function SuccessDefaults({
  providerId,
  result,
}: {
  providerId: string;
  result: ProviderSetupResult;
}) {
  const { t } = useTranslation();
  const configSummary = useAIConfigStore((state) => state.configSummary);
  const setProviderDefault = useAIConfigStore((state) => state.setProviderDefault);
  const [error, setError] = useState<string | null>(null);

  const providerModels = (configSummary?.models ?? []).filter(
    (model) => model.provider_id === providerId && model.is_active,
  );
  const catalog: ModelPickerProvider[] = [
    {
      id: providerId,
      name: result.provider.name,
      models: providerModels.map((model) => ({
        id: model.model_name,
        name: model.name || model.model_name,
        capabilities: model.capabilities?.length ? model.capabilities : ['text'],
      })),
    },
  ];

  const bind = async (task: string, modelName: string) => {
    setError(null);
    try {
      await setProviderDefault(providerId, modelName, task);
    } catch (err: any) {
      setError(err?.message ?? 'Failed to assign model');
    }
  };

  const slotValue = (assigned: string | null | undefined): string => {
    if (assigned && providerModels.some((model) => model.model_name === assigned)) {
      return assigned;
    }
    return '';
  };

  const hasCap = (cap: string) =>
    providerModels.some((model) => model.capabilities?.includes(cap as any));
  const rows = [
    {
      task: 'default',
      label: t('settings.ai.setup.slot_chat'),
      assigned: result.assigned_chat_model,
    },
    ...(hasCap('vision')
      ? [
          {
            task: 'ocr',
            label: t('settings.ai.setup.slot_vision'),
            assigned: result.assigned_vision_model,
          },
        ]
      : []),
    ...(hasCap('stt')
      ? [
          {
            task: 'transcription',
            label: t('settings.ai.setup.slot_stt'),
            assigned: result.assigned_stt_model,
          },
        ]
      : []),
  ];

  if (providerModels.length === 0) {
    return (
      <p className="text-xs text-gray-500 dark:text-dark-muted">
        {t('settings.ai.setup.no_defaults')}
      </p>
    );
  }

  return (
    <div className="space-y-2 rounded-lg border border-gray-200 p-3 dark:border-dark-border">
      <h3 className="text-sm font-semibold text-gray-900 dark:text-dark-text">
        {t('settings.ai.setup.default_models')}
      </h3>
      {rows.map((row) => (
        <ModelPicker
          key={row.task}
          providers={catalog}
          value={slotValue(row.assigned)}
          onChange={(modelName) => void bind(row.task, modelName).catch(() => undefined)}
          clearable={false}
          label={row.label}
        />
      ))}
      {error ? <p className="text-xs text-red-600 dark:text-red-400">{error}</p> : null}
    </div>
  );
}

export const ProviderSetupModal: React.FC<{
  open: boolean;
  onClose: () => void;
  onManual: () => void;
}> = ({ open, onClose, onManual }) => {
  const { t } = useTranslation();
  const setupProvider = useAIConfigStore((state) => state.setupProvider);

  const [presets, setPresets] = useState<Record<string, ProviderPreset> | null>(null);
  const [order, setOrder] = useState<string[]>([]);
  const [phase, setPhase] = useState<Phase>('tiles');
  const [presetKey, setPresetKey] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [advancedBase, setAdvancedBase] = useState('');
  const [isLocal, setIsLocal] = useState(false);
  const [country, setCountry] = useState('');
  const [error, setError] = useState<ProviderSetupErrorDetail | null>(null);
  const [plainError, setPlainError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<ProviderSetupResult | null>(null);
  const [resultName, setResultName] = useState('');

  useEffect(() => {
    if (!open || presets) return;
    aiConfigApi
      .listProviderPresets()
      .then((data) => {
        setPresets(data.presets);
        setOrder(data.order);
      })
      .catch(() => setPresets({}));
  }, [open, presets]);

  if (!open) return null;

  const preset: ProviderPreset | null = presetKey ? presets?.[presetKey] ?? null : null;

  const openForm = (key: string) => {
    setPresetKey(key);
    setName('');
    setApiKey('');
    setAdvancedBase(presets?.[key]?.base_url ?? '');
    setIsLocal(presets?.[key]?.local ?? false);
    setCountry('');
    setError(null);
    setPlainError(null);
    setPhase('form');
  };

  const backToTiles = () => {
    setPhase('tiles');
    setPresetKey(null);
    setError(null);
    setPlainError(null);
  };

  const baseEdited =
    preset !== null &&
    !preset.fixed_base &&
    advancedBase.trim() !== '' &&
    advancedBase.trim() !== preset.base_url;
  const forcedManual =
    preset !== null && (baseEdited || (!preset.local && isLocal) || country.trim() !== '');

  const connect = async () => {
    if (preset === null || presetKey === null) return;
    if (forcedManual) {
      // §15: any edit to base/hosting/country routes through the manual form.
      onClose();
      onManual();
      return;
    }
    setPending(true);
    setError(null);
    setPlainError(null);
    try {
      const data = await setupProvider(presetKey, {
        api_key: preset.local ? null : apiKey.trim() || null,
        name: name.trim() || null,
      });
      setResultName(name.trim() || preset.name);
      setResult(data);
      setPhase('done');
    } catch (err: any) {
      const detail = setupErrorDetail(err);
      setError(detail);
      setPlainError(detail ? null : (err?.message ?? String(err)));
    } finally {
      setPending(false);
    }
  };

  if (phase === 'done' && result) {
    return (
      <Modal
        open
        onOpenChange={(o) => (!o ? onClose() : undefined)}
        title={t('settings.ai.setup.title')}
      >
        <div className="space-y-3">
          <p className="flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-dark-text">
            <Check className="h-4 w-4 text-green-600" aria-hidden />
            {t('settings.ai.setup.done', { name: resultName })}
          </p>
          <p className="text-xs text-gray-500 dark:text-dark-muted">
            {t('settings.ai.setup.models_persisted', { count: result.catalog_count })}
          </p>
          {result.curated_missed ? (
            <p className="text-xs text-amber-600 dark:text-amber-400">
              {t('settings.ai.setup.curated_missed')}
            </p>
          ) : null}
          <SuccessDefaults providerId={result.provider.id} result={result} />
          <div className="flex justify-end">
            <Button size="sm" variant="ghost" onClick={onClose}>
              {t('settings.ai.close')}
            </Button>
          </div>
        </div>
      </Modal>
    );
  }

  return (
    <Modal
      open
      onOpenChange={(o) => (!o ? onClose() : undefined)}
      title={
        phase === 'form' && preset
          ? t('settings.ai.setup.form_title', { name: preset.name })
          : t('settings.ai.setup.title')
      }
    >
      <div className="space-y-3">
        <p className="text-xs text-gray-500 dark:text-dark-muted">
          {t('settings.ai.setup.modal_hint')}
        </p>

        {phase === 'tiles' ? (
          <div
            className="grid grid-cols-2 gap-2 sm:grid-cols-3"
            role="group"
            aria-label={t('settings.ai.setup.title')}
          >
            {order.map((key) => {
              const tile = presets?.[key];
              if (!tile) return null;
              return (
                <button
                  key={key}
                  type="button"
                  className="flex h-auto flex-col items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-4 hover:bg-gray-50 dark:border-dark-border dark:bg-dark-surface dark:hover:bg-dark-bg"
                  onClick={() => openForm(key)}
                >
                  <ProviderLogo presetKey={key} label={tile.name} />
                  <span className="w-full truncate text-center text-sm font-medium text-gray-900 dark:text-dark-text">
                    {tile.name}
                  </span>
                  {tile.local ? (
                    <span className="text-[11px] text-gray-500 dark:text-dark-muted">
                      {t('settings.ai.local_kind')}
                    </span>
                  ) : null}
                </button>
              );
            })}
            <button
              type="button"
              className="flex h-auto flex-col items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-4 hover:bg-gray-50 dark:border-dark-border dark:bg-dark-surface dark:hover:bg-dark-bg"
              onClick={() => {
                onClose();
                onManual();
              }}
            >
              <Settings2 className="h-5 w-5 text-gray-400" aria-hidden />
              <span className="w-full truncate text-center text-sm font-medium text-gray-900 dark:text-dark-text">
                {t('settings.ai.setup.custom')}
              </span>
            </button>
          </div>
        ) : null}

        {phase === 'form' && preset ? (
          <>
            <ol className="list-decimal space-y-1 pl-5 text-xs">
              {(preset.steps ?? []).map((step, index) => (
                <li
                  key={index}
                  className="flex items-start justify-between gap-2 text-gray-500 dark:text-dark-muted"
                >
                  <span>{step.replace('{url}', preset.key_url ?? '')}</span>
                  <button
                    type="button"
                    className="shrink-0 rounded p-1"
                    aria-label={t('settings.ai.setup.copy_step')}
                    title={t('settings.ai.setup.copy_step')}
                    onClick={() => void navigator.clipboard.writeText(step)}
                  >
                    <Copy className="h-3 w-3" aria-hidden />
                  </button>
                </li>
              ))}
            </ol>
            {preset.free_tier_note ? (
              <p className="text-xs text-gray-500 dark:text-dark-muted">
                {preset.free_tier_note}
              </p>
            ) : null}
            {preset.key_url ? (
              <a
                className="inline-flex items-center gap-1 text-xs text-blue-600 underline dark:text-blue-400"
                href={preset.key_url}
                target="_blank"
                rel="noreferrer"
              >
                <ExternalLink className="h-3 w-3" aria-hidden />
                {t('settings.ai.setup.get_key')}
              </a>
            ) : null}
            <label className="block space-y-1 text-sm">
              <span className="text-gray-500 dark:text-dark-muted">
                {t('settings.ai.setup.connection_name')}
              </span>
              <input
                className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 dark:border-dark-border dark:bg-dark-surface dark:text-dark-text"
                value={name}
                placeholder={preset.name}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            {!preset.local ? (
              <label className="block space-y-1 text-sm">
                <span className="text-gray-500 dark:text-dark-muted">
                  {t('settings.ai.api_key')}
                </span>
                <input
                  className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 font-mono dark:border-dark-border dark:bg-dark-surface dark:text-dark-text"
                  type="password"
                  autoComplete="off"
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  autoFocus
                />
              </label>
            ) : (
              <p className="text-xs text-gray-500 dark:text-dark-muted">
                {t('settings.ai.setup.no_key_needed')}
              </p>
            )}
            <details className="rounded-lg border border-gray-200 px-3 py-2 dark:border-dark-border">
              <summary className="cursor-pointer text-sm font-medium">
                {t('settings.ai.setup.advanced')}
              </summary>
              <div className="space-y-3 pt-2">
                <label className="block space-y-1 text-sm">
                  <span className="text-gray-500 dark:text-dark-muted">
                    {t('settings.ai.base_url')}
                  </span>
                  <input
                    className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 font-mono disabled:opacity-80 dark:border-dark-border dark:bg-dark-surface dark:text-dark-text"
                    value={advancedBase}
                    disabled={preset.fixed_base}
                    onChange={(event) => setAdvancedBase(event.target.value)}
                  />
                </label>
                {!preset.fixed_base ? (
                  <p className="text-[11px] text-gray-500 dark:text-dark-muted">
                    {t('settings.ai.setup.base_edit_hint')}
                  </p>
                ) : null}
                <HostingToggle local={isLocal} onChange={setIsLocal} disabled={preset.local} />
                <CountrySelect country={country} onChange={setCountry} />
              </div>
            </details>
            <SetupErrorPanel error={error} plainError={plainError} />
            <div className="flex items-center justify-between gap-2">
              <Button variant="ghost" size="sm" onClick={backToTiles}>
                {t('settings.ai.setup.choose_another')}
              </Button>
              <Button
                size="sm"
                disabled={(!preset.local && apiKey.trim().length === 0) || pending}
                onClick={() => void connect().catch(() => undefined)}
              >
                {pending ? <Loader2 className="animate-spin" aria-hidden /> : <Check aria-hidden />}
                {t('settings.ai.setup.automatically')}
              </Button>
            </div>
          </>
        ) : null}
      </div>
    </Modal>
  );
};
