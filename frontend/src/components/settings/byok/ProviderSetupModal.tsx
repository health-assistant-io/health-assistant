import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Banknote, Check, House, Loader2 } from 'lucide-react';
import { ModelPicker, type ModelPickerProvider } from '@neuronection/assistant-ui';
import {
  COUNTRIES,
  countryDisplayName,
} from '@neuronection/assistant-ui/countries';
import {
  ProviderSetupForm,
  type ProviderSetupFormValue,
} from '@neuronection/assistant-ui/provider-setup-form';
import {
  ProviderSetupTiles,
  ProviderSetupManualTile,
} from '@neuronection/assistant-ui/provider-setup-tiles';
import { SetupReview, type SetupReviewOptions } from '@neuronection/assistant-ui/setup-review';
import { SetupSteps } from '@neuronection/assistant-ui/setup-steps';
import { SetupAssignments } from '@neuronection/assistant-ui/setup-assignments';
import {
  aiConfigApi,
  DECISION_DEFAULT_TASK_TYPE,
  setupErrorDetail,
  type AIModel,
  type ProviderPreset,
  type ProviderSetupErrorDetail,
  type ProviderSetupResult,
} from '../../../api/aiConfig';
import { useAIConfigStore } from '../../../store/slices/aiConfigSlice';
import { Button } from '../../ui/Button';
import { Modal } from '../../ui/Modal';
import { SearchableDropdown } from '../../ui/SearchableDropdown';

import { ERROR_CODES, SetupErrorPanel } from './setupErrors';
import { CAP_ICONS, guessCaps } from './ReRunSetupDialog';

type Phase = 'tiles' | 'form' | 'review' | 'done';

/**
 * Capability chips for a preset tile (plan 26 §13.3 follow-up): derived
 * from the API preset data — chat type → chat (+ vision where the
 * preferred model carries it, + stt where stt_model exists),
 * decision_models → decision, keyless-local → local. Labels via i18n.
 */
function chipsForPreset(
  preset: ProviderPreset,
  t: (key: string) => string,
): { key: string; label: string }[] {
  const chips: { key: string; label: string }[] = [];
  if (preset.provider_type !== 'decision') {
    chips.push({ key: 'chat', label: t('settings.ai.chip_chat') });
    if (preset.preferred_model?.caps.includes('vision')) {
      chips.push({ key: 'vision', label: t('settings.ai.chip_vision') });
    }
    if (preset.stt_model) {
      chips.push({ key: 'stt', label: t('settings.ai.chip_stt') });
    }
  }
  if (preset.decision_models && preset.decision_models.length > 0) {
    chips.push({ key: 'decision', label: t('settings.ai.chip_decision') });
  }
  if (preset.local) {
    chips.push({ key: 'local', label: t('settings.ai.chip_local') });
  }
  return chips;
}

const SCOPE_MAP = {
  global: 'SYSTEM',
  tenant: 'TENANT',
  user: 'USER',
} as const;

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
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? 'en';
  const options = COUNTRIES.map((entry) => ({
    value: entry.code,
    label: `${entry.flag} ${countryDisplayName(entry.code, locale)}`,
  }));
  return (
    <SearchableDropdown
      options={options}
      value={country}
      onChange={onChange}
      label={t('settings.ai.country_label')}
      placeholder={t('settings.ai.country_placeholder')}
      searchPlaceholder={t('settings.ai.country_search')}
      searchLabel={t('settings.ai.country_search')}
      emptyLabel={t('settings.ai.country_empty')}
      clearable
      clearLabel={t('settings.ai.country_clear')}
    />
  );
}

/** Per-slot rebind pickers shown after a successful setup (§15 success panel).
 *  Slot mapping is health's: chat→default, vision→ocr, stt→transcription;
 *  decision slots (plan 26) list only the provider's decision models. */
function SuccessDefaults({
  providerId,
  result,
}: {
  providerId: string;
  result: ProviderSetupResult;
}) {
  const { t } = useTranslation();
  const setProviderDefault = useAIConfigStore((state) => state.setProviderDefault);
  const [models, setModels] = useState<AIModel[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    aiConfigApi
      .getProviderWithModels(providerId)
      .then((data) => {
        if (!cancelled) setModels(data.models ?? []);
      })
      .catch(() => {
        if (!cancelled) setModels([]);
      });
    return () => {
      cancelled = true;
    };
  }, [providerId]);

  if (models === null) return null;
  const providerModels = models.filter((model) => model.is_active);
  const decisionModels = providerModels.filter(
    (model) => Boolean(model.decision_transport) || model.capabilities?.includes('decision'),
  );
  const decisionCatalog: ModelPickerProvider[] =
    decisionModels.length > 0
      ? [
          {
            id: providerId,
            name: result.provider.name,
            models: decisionModels.map((model) => ({
              id: model.model_name,
              name: model.name || model.model_name,
              capabilities: model.capabilities?.length ? model.capabilities : ['decision'],
            })),
          },
        ]
      : [];
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
    ...(hasCap('embeddings')
      ? [
          {
            task: 'embed',
            label: t('settings.ai.setup.slot_embeddings'),
            assigned: result.assigned_embeddings_model,
          },
        ]
      : []),
  ];
  // ADR-0036 §3: setup binds the `decision` default slot only — one
  // assignment, one review row; kind slots inherit.
  const decisionRows = [
    {
      task: DECISION_DEFAULT_TASK_TYPE,
      label: t('settings.ai.task_decision_default'),
      assigned: result.assigned_decision_slots?.[DECISION_DEFAULT_TASK_TYPE] ?? null,
    },
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
      {decisionModels.length > 0
        ? decisionRows.map((row) => (
            <ModelPicker
              key={row.task}
              providers={decisionCatalog}
              value={slotValue(row.assigned)}
              onChange={(modelName) => void bind(row.task, modelName).catch(() => undefined)}
              clearable={false}
              label={row.label}
            />
          ))
        : null}
      {error ? <p className="text-xs text-red-600 dark:text-red-400">{error}</p> : null}
    </div>
  );
}

export const ProviderSetupModal: React.FC<{
  open: boolean;
  onClose: () => void;
  onManual: () => void;
  scope?: 'global' | 'tenant' | 'user';
}> = ({ open, onClose, onManual, scope = 'user' }) => {
  const { t } = useTranslation();
  const setupProvider = useAIConfigStore((state) => state.setupProvider);
  const createProvider = useAIConfigStore((state) => state.createProvider);
  // Live "now:" context for the review's assignment rows — the store's
  // summary is refreshed by the connect call itself, so entering the
  // review reads post-connect assignments and model display names.
  const configSummary = useAIConfigStore((state) => state.configSummary);

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
  // Review step (parity with the re-run dialog): connect binds nothing —
  // what registers and what fills the empty defaults is chosen here.
  const [reviewOptions, setReviewOptions] = useState<SetupReviewOptions>({
    curatedIds: [],
    bindChat: true,
    bindVision: true,
    bindStt: true,
    bindDecision: true,
  });
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<ProviderSetupErrorDetail | null>(null);
  const [applyPlainError, setApplyPlainError] = useState<string | null>(null);

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
  // Decision models never ride the curated cards (ADR-0036 §3: one default
  // slot, kinds inherit) — the switch binds the preset's `decision_default`
  // only, so a decision section without one stays hidden (it could not
  // bind anything).
  const decisionCandidate =
    preset?.decision_models?.find((entry) => entry.id === preset.decision_default) ?? null;
  const decisionDeclared = decisionCandidate !== null;

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
    setPending(true);
    setError(null);
    setPlainError(null);
    try {
      // Connect + register only — binds stay off so the review step
      // decides what fills the defaults (same contract as re-setup).
      const data = await setupProvider(presetKey, {
        api_key: preset.local ? null : apiKey.trim() || null,
        name: name.trim() || null,
        scope: SCOPE_MAP[scope],
        options: {
          bind_chat: false,
          bind_vision: false,
          bind_stt: false,
          bind_decision: false,
        },
      });
      setResultName(name.trim() || preset.name);
      setResult(data);
      setReviewOptions({
        curatedIds: preset.curated_models ?? [],
        bindChat: true,
        bindVision: true,
        bindStt: true,
        bindDecision: decisionDeclared,
      });
      setApplyError(null);
      setApplyPlainError(null);
      setPhase('review');
    } catch (err: any) {
      const detail = setupErrorDetail(err);
      setError(detail);
      setPlainError(detail ? null : (err?.message ?? String(err)));
    } finally {
      setPending(false);
    }
  };

  /** Apply the reviewed choices (second pass over the same preset): the
   *  curated "models to add" selection plus the per-capability fill-empty
   *  switches. Binding stays gap-fill backend-side — only empty defaults
   *  are filled, nothing existing is ever removed. */
  const applyReview = async () => {
    if (presetKey === null || preset === null || applying) return;
    setApplying(true);
    setApplyError(null);
    setApplyPlainError(null);
    try {
      const data = await setupProvider(presetKey, {
        api_key: preset.local ? null : apiKey.trim() || null,
        name: name.trim() || null,
        scope: SCOPE_MAP[scope],
        options: {
          curated_ids: preset.curated_models ? reviewOptions.curatedIds : undefined,
          bind_chat: reviewOptions.bindChat,
          bind_vision: reviewOptions.bindVision,
          bind_stt: reviewOptions.bindStt,
          bind_decision: reviewOptions.bindDecision && decisionDeclared,
        },
      });
      setResult(data);
      setPhase('done');
    } catch (err: any) {
      const detail = setupErrorDetail(err);
      setApplyError(detail);
      setApplyPlainError(detail ? null : (err?.message ?? String(err)));
    } finally {
      setApplying(false);
    }
  };

  /** Manual setup: create the provider row from the (possibly edited) form
   *  fields via the plain CRUD path — no catalog fetch, no model curation,
   *  no default binding. The user configures models by hand afterwards. */
  const connectManual = async () => {
    if (preset === null) return;
    setPending(true);
    setError(null);
    setPlainError(null);
    try {
      await createProvider({
        name: name.trim() || preset.name,
        provider_type: preset.provider_type,
        api_base: advancedBase.trim() || preset.base_url,
        is_active: true,
        is_local: isLocal,
        company_country: country.trim() || null,
        scope: SCOPE_MAP[scope],
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      });
      onClose();
    } catch (err: any) {
      setPlainError(err?.message ?? 'Failed to save provider');
    } finally {
      setPending(false);
    }
  };

  if (phase === 'review' && result && preset) {
    // Effective candidates mirror the backend's gap-fill resolution:
    // preferred model (when its card stays selected) → first selected
    // curated text model; vision rides the same model when capable, else
    // the first selected vision-capable card; stt must keep its card.
    const selectedCurated = (preset.curated_models ?? []).filter((id) =>
      reviewOptions.curatedIds.includes(id),
    );
    const preferredId = preset.preferred_model?.id ?? null;
    const textCandidate =
      (preferredId !== null && selectedCurated.includes(preferredId) ? preferredId : null) ??
      selectedCurated.find((id) => guessCaps(id).includes('text')) ??
      selectedCurated[0] ??
      null;
    const visionCandidate =
      (textCandidate !== null && guessCaps(textCandidate).includes('vision')
        ? textCandidate
        : null) ??
      selectedCurated.find((id) => guessCaps(id).includes('vision')) ??
      null;
    const sttCandidate =
      preset.stt_model && selectedCurated.includes(preset.stt_model) ? preset.stt_model : null;

    const modelLabels = Object.fromEntries(
      (configSummary?.models ?? []).map((model) => [model.model_name, model.name || model.model_name]),
    );
    const nowLabel = (slotTask: string) => {
      const model = configSummary?.[slotTask as 'default']?.model;
      const label = model ? (modelLabels[model.model_name] ?? model.model_name) : null;
      return t('settings.ai.setup.review.now', {
        model: label ?? t('settings.ai.setup.review.unset'),
      });
    };
    const fillsLabel = (candidate: string | null, slotTask: string) =>
      candidate === null
        ? t('settings.ai.setup.review.no_candidate')
        : `${nowLabel(slotTask)} · ${t('settings.ai.setup.review.fills', { model: candidate })}`;

    // Health slot mapping: chat→default, vision→ocr, stt→transcription;
    // the decision row reads the `decision` default slot (ADR-0036 §3).
    const assignmentRows = [
      ...(preset.provider_type !== 'decision'
        ? [
            {
              id: 'chat',
              label: t('settings.ai.setup.slot_chat'),
              sub: fillsLabel(textCandidate, 'default'),
              candidate: textCandidate,
              bind: reviewOptions.bindChat,
              set: (next: boolean) =>
                setReviewOptions((current) => ({ ...current, bindChat: next })),
            },
            {
              id: 'vision',
              label: t('settings.ai.setup.slot_vision'),
              sub: fillsLabel(visionCandidate, 'ocr'),
              candidate: visionCandidate,
              bind: reviewOptions.bindVision,
              set: (next: boolean) =>
                setReviewOptions((current) => ({ ...current, bindVision: next })),
            },
            ...(preset.stt_model
              ? [
                  {
                    id: 'stt',
                    label: t('settings.ai.setup.slot_stt'),
                    sub: fillsLabel(sttCandidate, 'transcription'),
                    candidate: sttCandidate,
                    bind: reviewOptions.bindStt,
                    set: (next: boolean) =>
                      setReviewOptions((current) => ({ ...current, bindStt: next })),
                  },
                ]
              : []),
          ]
        : []),
      ...(decisionDeclared
        ? [
            {
              id: 'decision',
              label: t('settings.ai.task_decision_default'),
              sub: fillsLabel(decisionCandidate!.name, DECISION_DEFAULT_TASK_TYPE),
              candidate: decisionCandidate!.name,
              bind: reviewOptions.bindDecision,
              set: (next: boolean) =>
                setReviewOptions((current) => ({ ...current, bindDecision: next })),
            },
          ]
        : []),
    ];

    return (
      <Modal
        open
        onOpenChange={(o) => (!o ? onClose() : undefined)}
        title={t('settings.ai.setup.title')}
        footer={
          <div className="flex w-full items-center justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              disabled={applying}
              onClick={() => setPhase('form')}
            >
              {t('common.back')}
            </Button>
            <Button
              size="sm"
              disabled={applying}
              onClick={() => void applyReview().catch(() => undefined)}
            >
              {applying ? <Loader2 className="animate-spin" aria-hidden /> : <Check aria-hidden />}
              {t('settings.ai.setup.review.apply')}
            </Button>
          </div>
        }
      >
        <div className="space-y-3">
          <p className="text-xs text-gray-500 dark:text-dark-muted">
            {t('settings.ai.setup.review.intro', {
              name: resultName || preset.name,
              count: result.catalog_count,
            })}
          </p>

          <SetupSteps
            steps={[
              { id: 'connect', label: t('settings.ai.setup.step.connect') },
              { id: 'defaults', label: t('settings.ai.setup.step.defaults') },
            ]}
            current={1}
            label={t('settings.ai.setup.step.progress')}
          />

          {(preset.curated_models ?? []).length > 0 ? (
            <section className="space-y-2">
              <div>
                <h3 className="text-sm font-semibold text-gray-900 dark:text-dark-text">
                  {t('settings.ai.setup.review.models_title')}
                </h3>
                <p className="text-xs text-gray-500 dark:text-dark-muted">
                  {t('settings.ai.setup.review.models_hint')}
                </p>
              </div>
              <SetupReview
                models={(preset.curated_models ?? []).map((modelId) => ({
                  id: modelId,
                  name: modelId,
                  caps: guessCaps(modelId),
                  selected: reviewOptions.curatedIds.includes(modelId),
                }))}
                slots={[]}
                capIcons={CAP_ICONS}
                modelsLabel={t('settings.ai.setup.review.models_title')}
                footnote={t('settings.ai.setup.review.models_footnote')}
                onOptionsChange={(options) =>
                  setReviewOptions((current) => ({ ...current, curatedIds: options.curatedIds }))
                }
              />
            </section>
          ) : null}

          <SetupAssignments
            title={t('settings.ai.setup.review.assignments_title')}
            hint={t('settings.ai.setup.review.assignments_hint')}
            rows={assignmentRows.map((row) => ({
              id: row.id,
              label: row.label,
              hint: row.sub,
              checked: row.bind,
              disabled: row.candidate === null,
              onCheckedChange: row.set,
            }))}
          />

          <SetupErrorPanel error={applyError} plainError={applyPlainError} />
        </div>
      </Modal>
    );
  }

  if (phase === 'done' && result) {
    return (
      <Modal
        open
        onOpenChange={(o) => (!o ? onClose() : undefined)}
        title={t('settings.ai.setup.title')}
        footer={
          <div className="flex items-center justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={onClose}>
              {t('settings.ai.close')}
            </Button>
          </div>
        }
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
      footer={
        phase === 'form' && preset ? (
          <div className="flex w-full items-center justify-between gap-2">
            <Button
              variant="ghost"
              size="sm"
              disabled={pending}
              title={t('settings.ai.setup.manually_hint')}
              onClick={() => void connectManual().catch(() => undefined)}
            >
              {t('settings.ai.setup.manually')}
            </Button>
          </div>
        ) : (
          <div className="flex w-full items-center justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={onClose}>
              {t('settings.ai.cancel')}
            </Button>
          </div>
        )
      }
    >
      <div className="space-y-3">
        <p className="text-xs text-gray-500 dark:text-dark-muted">
          {t('settings.ai.setup.modal_hint')}
        </p>

        <SetupSteps
          steps={[
            { id: 'connect', label: t('settings.ai.setup.step.connect') },
            { id: 'defaults', label: t('settings.ai.setup.step.defaults') },
          ]}
          current={0}
          label={t('settings.ai.setup.step.progress')}
        />

        {phase === 'tiles' ? (
          <ProviderSetupTiles
            presets={order.map((key) => {
              const tile = presets?.[key];
              if (!tile) return { key, name: key };
              return { key, name: tile.name, chips: chipsForPreset(tile, t) };
            })}
            onSelect={openForm}
            label={t('settings.ai.setup.title')}
            localLabel={t('settings.ai.local_kind')}
          >
            <ProviderSetupManualTile
              onClick={() => {
                onClose();
                onManual();
              }}
            >
              {t('settings.ai.setup.custom')}
            </ProviderSetupManualTile>
          </ProviderSetupTiles>
        ) : null}

        {phase === 'form' && preset ? (
          <>
            <ProviderSetupForm
              preset={{
                key: presetKey ?? undefined,
                name: preset.name,
                keyUrl: preset.key_url ?? undefined,
                steps: preset.steps ?? undefined,
                freeTierNote: preset.free_tier_note ?? undefined,
                fixedBase: preset.fixed_base,
                baseUrl: preset.base_url,
                local: preset.local,
              }}
              value={{ name, apiKey, baseUrl: advancedBase }}
              onChange={(value: ProviderSetupFormValue) => {
                setName(value.name);
                setApiKey(value.apiKey);
                setAdvancedBase(value.baseUrl ?? '');
              }}
              testing={pending}
              error={
                error
                  ? { code: error.code, suspectedVendor: error.suspected_vendor ?? null }
                  : plainError
                    ? { code: 'unknown' }
                    : null
              }
              errorLabels={{
                ...Object.fromEntries(
                  ERROR_CODES.map((code) => [code, t(`settings.ai.setup.errors.${code}`)]),
                ),
                unknown: plainError ?? t('settings.ai.setup.errors.unknown'),
              }}
              suspectedVendorHint={t('settings.ai.setup.errors.suspected_vendor', {
                vendor: error?.suspected_vendor ?? '',
              })}
              advancedExtra={
                <>
                  <HostingToggle local={isLocal} onChange={setIsLocal} disabled={preset.local} />
                  <CountrySelect country={country} onChange={setCountry} />
                </>
              }
              copyLabel={t('settings.ai.setup.copy_step')}
              getKeyLabel={t('settings.ai.setup.get_key')}
              nameLabel={t('settings.ai.setup.connection_name')}
              apiKeyLabel={t('settings.ai.api_key')}
              noKeyLabel={t('settings.ai.setup.no_key_needed')}
              advancedLabel={t('settings.ai.setup.advanced')}
              baseUrlLabel={t('settings.ai.base_url')}
              baseUrlHint={t('settings.ai.setup.base_edit_hint')}
              submitLabel={t('settings.ai.setup.automatically')}
              cancelLabel={t('settings.ai.setup.choose_another')}
              onSubmit={() => {
                if (forcedManual) {
                  void connectManual().catch(() => undefined);
                } else {
                  void connect().catch(() => undefined);
                }
              }}
              onCancel={backToTiles}
            />
          </>
        ) : null}
      </div>
    </Modal>
  );
};
