import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Eye, FileText, AudioLines } from 'lucide-react';
import {
  ModelRegistry,
  type CapabilityDescriptor,
  type ModelRegistryDraft,
  type ModelRegistryModel,
  type ModelRegistryPatch,
} from '@neuronection/assistant-ui';
import { useAIConfigStore } from '../../store/slices/aiConfigSlice';
import { AIModelCapability } from '../../api/aiConfig';
import { useUIStore } from '../../store/slices/uiSlice';

const CAP_ICONS: Record<AIModelCapability, typeof FileText> = {
  text: FileText,
  vision: Eye,
  audio_input: AudioLines,
};

/** App-side capability inference for provider catalogs that don't report
 *  capabilities (mirrors app/ai/providers/capabilities.py semantics). */
function inferRemoteCaps(externalId: string): string[] {
  const id = externalId.toLowerCase();
  if (/whisper|audio|speech|tts|stt/.test(id)) return ['audio_input'];
  if (/vision|vl|llava|omni|gpt-4o|gpt-4\.1|gpt-4-turbo|multimodal/.test(id))
    return ['text', 'vision'];
  return ['text'];
}

interface RemoteState {
  models: { id: string; caps: string[] }[];
  state: 'loading' | 'error' | 'ready';
  error: string | null;
}

export const ModelsTab: React.FC = () => {
  const { t } = useTranslation();
  const showConfirmation = useUIStore((state) => state.showConfirmation);
  const {
    providers,
    models,
    createModel,
    updateModel,
    deleteModel,
    fetchExternalModels,
    error,
    clearError,
  } = useAIConfigStore();

  const [expandedProviderId, setExpandedProviderId] = useState<string | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);
  const [remote, setRemote] = useState<RemoteState>({ models: [], state: 'ready', error: null });

  useEffect(() => {
    if (!expandedProviderId && providers.length > 0) {
      setExpandedProviderId(providers[0].id);
    }
  }, [expandedProviderId, providers]);

  useEffect(() => {
    if (!expandedProviderId) return;
    const provider = providers.find((p) => p.id === expandedProviderId);
    if (!provider || provider.provider_type !== 'openai') {
      setRemote({ models: [], state: 'ready', error: null });
      return;
    }
    let cancelled = false;
    setRemote({ models: [], state: 'loading', error: null });
    fetchExternalModels(expandedProviderId)
      .then((list) => {
        if (!cancelled) {
          setRemote({
            models: (list ?? []).map((m: any) => ({ id: m.id, caps: inferRemoteCaps(m.id) })),
            state: 'ready',
            error: null,
          });
        }
      })
      .catch((err: any) => {
        if (!cancelled) {
          setRemote({ models: [], state: 'error', error: err?.message ?? 'Failed to fetch models' });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [expandedProviderId, providers, fetchExternalModels, retryNonce]);

  const registryProviders = providers.map((provider) => ({
    id: provider.id,
    name: provider.name,
    type: provider.provider_type,
    baseUrl: provider.api_base || undefined,
  }));

  const registryModels: ModelRegistryModel[] = models.map((model) => ({
    id: model.id,
    providerId: model.provider_id,
    externalId: model.model_name,
    label: model.name || undefined,
    caps: model.capabilities?.length ? model.capabilities : ['text'],
    enabled: model.is_active,
    temperature: model.temperature ?? null,
    maxTokens: model.max_tokens ?? null,
    extra: model.description ? { description: model.description } : undefined,
  }));

  const capDescriptors: CapabilityDescriptor[] = (
    ['text', 'vision', 'audio_input'] as AIModelCapability[]
  ).map((cap) => ({
    value: cap,
    label: t(`settings.ai.caps_${cap}`),
    icon: CAP_ICONS[cap],
  }));

  const handleAdd = async (providerId: string, draft: ModelRegistryDraft) => {
    await createModel(providerId, {
      name: draft.label?.trim() || draft.externalId,
      model_name: draft.externalId,
      description: draft.extra?.description?.trim() || undefined,
      capabilities: draft.caps as AIModelCapability[],
      is_active: true,
      max_tokens: draft.maxTokens ?? undefined,
      temperature: draft.temperature ?? undefined,
    });
  };

  const handleUpdate = async (model: ModelRegistryModel, patch: ModelRegistryPatch) => {
    await updateModel(model.id, {
      ...(patch.label !== undefined ? { name: patch.label } : {}),
      ...(patch.caps !== undefined ? { capabilities: patch.caps as AIModelCapability[] } : {}),
      ...(patch.temperature !== undefined ? { temperature: patch.temperature } : {}),
      ...(patch.maxTokens !== undefined ? { max_tokens: patch.maxTokens } : {}),
      ...(patch.extra && 'description' in patch.extra
        ? { description: patch.extra.description.trim() || null }
        : {}),
    });
  };

  const handleDelete = (model: ModelRegistryModel) => {
    showConfirmation({
      title: t('settings.ai.delete_model'),
      message: t('settings.ai.delete_model_confirm'),
      confirmLabel: t('settings.ai.delete_model'),
      cancelLabel: t('settings.ai.cancel'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        await deleteModel(model.id);
      },
    });
  };

  return (
    <div className="space-y-4">
      <p className="text-sm text-gray-500 dark:text-dark-muted">{t('settings.ai.hint')}</p>
      <ModelRegistry
        providers={registryProviders}
        models={registryModels}
        caps={capDescriptors}
        expandedProviderId={expandedProviderId}
        onExpandedProviderChange={setExpandedProviderId}
        remoteModels={remote.models}
        remoteState={remote.state}
        remoteError={remote.error}
        onRetryRemote={() => setRetryNonce((n) => n + 1)}
        onAddModel={(pid, draft) => void handleAdd(pid, draft).catch(() => undefined)}
        onUpdateModel={(model, patch) => void handleUpdate(model, patch).catch(() => undefined)}
        onDeleteModel={handleDelete}
        onToggleEnabled={(model, enabled) =>
          void updateModel(model.id, { is_active: enabled }).catch(() => undefined)
        }
        enableLabel={t('settings.ai.enabled')}
        extraFields={[
          {
            key: 'description',
            label: t('settings.ai.description'),
            placeholder: t('settings.ai.description_placeholder'),
            multiline: true,
          },
        ]}
        capsLabel={t('settings.ai.caps_label')}
        capsHint={t('settings.ai.caps_hint')}
        temperatureLabel={t('settings.ai.temperature')}
        maxTokensLabel={t('settings.ai.max_tokens')}
        labelLabel={t('settings.ai.display_label')}
        saveLabel={t('settings.ai.save')}
        cancelLabel={t('settings.ai.cancel')}
        addLabel={t('settings.ai.add_model')}
        addAllLabel={t('settings.ai.add_all')}
        addTitle={t('settings.ai.add_model')}
        editTitle={t('settings.ai.edit_model')}
        selectModelLabel={t('settings.ai.select_model')}
        manualIdToggleLabel={t('settings.ai.manual_id')}
        editLabel={t('settings.ai.edit_model')}
        removeLabel={t('settings.ai.remove')}
        missingLabel={t('settings.ai.missing')}
        searchPlaceholder={t('settings.ai.search_models')}
        searchLabel={t('settings.ai.search_models')}
        emptyProviderLabel={t('settings.ai.empty_provider')}
        externalIdRequiredLabel={t('settings.ai.id_required')}
        remoteEmptyLabel={t('settings.ai.remote_empty')}
        remoteLoadingLabel={t('settings.ai.remote_loading')}
        retryLabel={t('settings.ai.retry')}
        customOptionLabel={t('settings.ai.custom_option')}
        providersEmptyLabel={t('settings.ai.providers_empty')}
        addDraftLabel={t('settings.ai.add_model')}
      />
      {error ? (
        <div className="flex items-center justify-between rounded-lg border border-red-200 bg-red-100 p-3 dark:border-red-900/50 dark:bg-red-900/30">
          <span className="text-sm text-red-700 dark:text-red-300">{error}</span>
          <button
            onClick={clearError}
            className="px-2 py-1 text-xs font-bold underline"
          >
            {t('settings.ai.dismiss')}
          </button>
        </div>
      ) : null}
    </div>
  );
};
