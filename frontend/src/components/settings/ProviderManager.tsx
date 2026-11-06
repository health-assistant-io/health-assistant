import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Pencil, PlugZap, Plus, Power, Save, Sparkles, Trash2 } from 'lucide-react';
import {
  CUSTOM_PRESET_KEY,
  ErrorBanner,
  ProviderCard,
  ProviderForm,
  SetupFirstRunCard,
  type ConnectionTestStatus,
} from '@neuronection/assistant-ui';
import {
  COUNTRIES,
  countryDisplayName,
  getCountryFlag,
} from '@neuronection/assistant-ui/countries';
import { useAIConfigStore } from '../../store/slices/aiConfigSlice';
import { aiConfigApi, AIProvider, type ProviderPreset } from '../../api/aiConfig';
import { defaultName } from './defaultName';
import { useUIStore } from '../../store/slices/uiSlice';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { ProviderSetupModal } from './byok/ProviderSetupModal';
import { ReRunSetupDialog } from './byok/ReRunSetupDialog';

interface ProviderManagerProps {
  scope?: 'global' | 'tenant' | 'user';
  userId?: string;
  tenantId?: string;
}

type TestState = {
  status: ConnectionTestStatus;
  error?: string | null;
  modelCount?: number;
};

export const ProviderManager: React.FC<ProviderManagerProps> = ({
  scope = 'user',
  userId,
  tenantId,
}) => {
  const { t } = useTranslation();
  const showConfirmation = useUIStore((state) => state.showConfirmation);
  const {
    providers,
    models,
    updateProvider,
    deleteProvider,
    fetchExternalModels,
    error,
    clearError,
  } = useAIConfigStore();

  const [dialog, setDialog] = useState<{ provider: AIProvider | null } | null>(null);
  const [setupOpen, setSetupOpen] = useState(false);
  const [rerunProvider, setRerunProvider] = useState<AIProvider | null>(null);
  const [tests, setTests] = useState<Record<string, TestState>>({});

  const runTest = async (provider: AIProvider) => {
    setTests((current) => ({ ...current, [provider.id]: { status: 'testing' } }));
    try {
      const models = await fetchExternalModels(provider.id);
      setTests((current) => ({
        ...current,
        [provider.id]: { status: 'ok', modelCount: models?.length ?? 0 },
      }));
    } catch (err: any) {
      setTests((current) => ({
        ...current,
        [provider.id]: { status: 'fail', error: err?.message ?? 'Failed' },
      }));
    }
  };

  const handleDelete = (provider: AIProvider) => {
    showConfirmation({
      title: t('settings.ai.delete_provider'),
      message: t('settings.ai.delete_provider_confirm'),
      confirmLabel: t('settings.ai.delete_provider'),
      cancelLabel: t('settings.ai.cancel'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        await deleteProvider(provider.id);
      },
    });
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500 dark:text-dark-muted">
          {t('settings.ai.providers_hint')}
        </p>
        <Button size="sm" onClick={() => setSetupOpen(true)}>
          <Plus aria-hidden />
          {t('settings.ai.add_provider')}
        </Button>
      </div>

      {providers.map((provider) => {
        const test = tests[provider.id];
        const status: ConnectionTestStatus =
          test?.status ?? 'idle';
        // Career parity: the connection test lives ONLY in the overflow
        // menu, and only when a registered model exists to test against
        // (health's registry is the shared store — career's fetchAllModels).
        const testing = status === 'testing';
        const registeredModels = models.filter(
          (m) => m.provider_id === provider.id,
        );
        const actions = [
          ...(provider.preset_key
            ? [
                {
                  key: 'setup',
                  label: t('settings.ai.setup.automatically'),
                  icon: Sparkles,
                  onSelect: () => setRerunProvider(provider),
                },
              ]
            : []),
          {
            key: 'edit',
            label: t('settings.ai.edit'),
            icon: Pencil,
            onSelect: () => setDialog({ provider }),
          },
          ...(registeredModels.length > 0
            ? [
                {
                  key: 'test',
                  label: t('settings.ai.test'),
                  icon: PlugZap,
                  disabled: testing,
                  pending: testing,
                  onSelect: () => void runTest(provider).catch(() => undefined),
                },
              ]
            : []),
          {
            key: 'toggle',
            label: provider.is_active
              ? t('settings.ai.disable')
              : t('settings.ai.enable'),
            icon: Power,
            onSelect: () =>
              void updateProvider(provider.id, { is_active: !provider.is_active }).catch(
                () => undefined,
              ),
          },
          {
            key: 'delete',
            label: t('settings.ai.delete_provider'),
            icon: Trash2,
            danger: true,
            onSelect: () => handleDelete(provider),
          },
        ];
        return (
          <ProviderCard
            key={provider.id}
            name={provider.name}
            logoPreset={provider.preset_key ?? undefined}
            nameTitle={provider.api_base}
            subtitle={provider.api_base}
            status={status}
            idleLabel={t('settings.ai.not_tested')}
            okLabel={t('settings.ai.test_ok')}
            failLabel={t('settings.ai.test_failed')}
            errorMessage={test?.error ?? null}
            meta={t('settings.ai.models_count', {
              count: registeredModels.length,
            })}
            badges={[
              { key: 'type', label: provider.provider_type },
              {
                key: 'hosting',
                label: provider.is_local
                  ? t('settings.ai.local_kind')
                  : t('settings.ai.cloud_kind'),
                testId: `provider-hosting-${provider.id}`,
              },
              {
                key: 'scope',
                label: !provider.user_id && !provider.tenant_id
                  ? t('settings.ai.scope_system')
                  : provider.tenant_id && !provider.user_id
                    ? t('settings.ai.scope_org')
                    : t('settings.ai.scope_personal'),
              },
              ...(provider.company_country
                ? [
                    {
                      key: 'country',
                      label: `${getCountryFlag(provider.company_country)} ${provider.company_country}`,
                      title: provider.company_country,
                      testId: `provider-country-${provider.id}`,
                    },
                  ]
                : []),
              ...(!(provider.has_api_key || provider.api_key)
                ? [
                    {
                      key: 'key',
                      label: t('settings.ai.no_key'),
                      danger: true,
                      testId: `provider-key-${provider.id}`,
                    },
                  ]
                : []),
              ...(!provider.is_active
                ? [{ key: 'inactive', label: t('settings.ai.disabled'), danger: true }]
                : []),
            ]}
            actions={actions}
            actionsLabel={t('settings.ai.actions_aria', { name: provider.name })}
          />
        );
      })}

      {providers.length === 0 ? (
        <SetupFirstRunCard
          title={t('settings.ai.first_run_title')}
          body={t('settings.ai.no_providers')}
          setupLabel={t('settings.ai.add_provider')}
          onSetup={() => setSetupOpen(true)}
        />
      ) : null}

      {error ? (
        <ErrorBanner
          message={error}
          action={
            <button onClick={clearError} className="px-2 py-1 text-xs font-bold underline">
              {t('settings.ai.dismiss')}
            </button>
          }
        />
      ) : null}

      {dialog ? (
        <ProviderFormDialog
          provider={dialog.provider}
          scope={scope}
          userId={userId}
          tenantId={tenantId}
          existingNames={providers.map((provider) => provider.name)}
          onClose={() => setDialog(null)}
        />
      ) : null}

      <ProviderSetupModal
        open={setupOpen}
        scope={scope}
        onClose={() => setSetupOpen(false)}
        onManual={() => setDialog({ provider: null })}
      />

      {rerunProvider ? (
        <ReRunSetupDialog
          provider={rerunProvider}
          scope={scope}
          onClose={() => setRerunProvider(null)}
        />
      ) : null}
    </div>
  );
};

const PROVIDER_FORM_FIELDS = 'grid grid-cols-1 gap-4 md:grid-cols-2';

function ComplianceFields({
  companyName,
  onCompanyNameChange,
  companyWebsite,
  onCompanyWebsiteChange,
  typeLabel,
}: {
  companyName: string;
  onCompanyNameChange: (value: string) => void;
  companyWebsite: string;
  onCompanyWebsiteChange: (value: string) => void;
  typeLabel: string;
}) {
  const { t } = useTranslation();
  return (
    <>
      <label className="block space-y-1 text-sm">
        <span className="text-xs font-bold uppercase tracking-widest text-gray-400">
          {typeLabel}
        </span>
        <input
          type="text"
          value={companyName}
          onChange={(e) => onCompanyNameChange(e.target.value)}
          className="w-full rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm shadow-xs outline-hidden transition-all focus:ring-2 focus:ring-blue-500/20 dark:border-dark-border dark:bg-dark-surface dark:text-dark-text"
          placeholder="e.g. OpenAI, Inc."
        />
      </label>
      <label className="block space-y-1 text-sm">
        <span className="text-xs font-bold uppercase tracking-widest text-gray-400">
          {t('settings.ai.company_website')}
        </span>
        <input
          type="url"
          value={companyWebsite}
          onChange={(e) => onCompanyWebsiteChange(e.target.value)}
          className="w-full rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm shadow-xs outline-hidden transition-all focus:ring-2 focus:ring-blue-500/20 dark:border-dark-border dark:bg-dark-surface dark:text-dark-text"
          placeholder="https://openai.com"
        />
      </label>
    </>
  );
}

const ProviderFormDialog: React.FC<{
  provider: AIProvider | null;
  scope: 'global' | 'tenant' | 'user';
  userId?: string;
  tenantId?: string;
  existingNames: string[];
  onClose: () => void;
}> = ({ provider, scope, userId, tenantId, existingNames, onClose }) => {
  const { t, i18n } = useTranslation();
  const { createProvider, updateProvider } = useAIConfigStore();
  const editing = provider !== null;

  const [name, setName] = useState(provider?.name ?? '');
  const [nameDirty, setNameDirty] = useState(editing);
  const [providerType, setProviderType] = useState(provider?.provider_type ?? 'openai');
  const [baseUrl, setBaseUrl] = useState(
    provider?.api_base ?? 'https://api.openai.com/v1',
  );
  const [apiKey, setApiKey] = useState('');
  const [isActive, setIsActive] = useState(provider?.is_active ?? true);
  const [isLocal, setIsLocal] = useState(provider?.is_local ?? false);
  const [companyName, setCompanyName] = useState(provider?.company_name ?? '');
  const [companyWebsite, setCompanyWebsite] = useState(provider?.company_website ?? '');
  const [country, setCountry] = useState(provider?.company_country ?? '');
  const [engines, setEngines] = useState<ProviderPreset[]>([]);
  const [presetKey, setPresetKey] = useState(CUSTOM_PRESET_KEY);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // Engine catalog (create mode): the backend's preset surface — the same
  // §15 data the setup tiles render; health's processor-only tesseract
  // type joins as an app-declared local entry.
  useEffect(() => {
    if (editing) return;
    let cancelled = false;
    aiConfigApi
      .listProviderPresets()
      .then((data) => {
        if (!cancelled) {
          setEngines(
            data.order
              .filter((key) => data.presets[key])
              .map((key) => data.presets[key]),
          );
        }
      })
      .catch(() => {
        if (!cancelled) setEngines([]);
      });
    return () => {
      cancelled = true;
    };
  }, [editing]);

  const engineOptions = [
    ...engines
      .filter((engine) => engine.key !== 'tesseract')
      .map((engine) => ({
        key: engine.key,
        name: engine.name,
        local: Boolean(engine.local),
      })),
    { key: 'tesseract', name: t('settings.ai.type_tesseract'), local: true },
  ];

  const applyPreset = (key: string) => {
    setPresetKey(key);
    if (key === CUSTOM_PRESET_KEY) {
      setProviderType('openai');
      setBaseUrl('https://api.openai.com/v1');
      setIsLocal(false);
      return;
    }
    if (key === 'tesseract') {
      setProviderType('tesseract');
      setIsLocal(true);
      return;
    }
    const engine = engines.find((entry) => entry.key === key);
    if (!engine) return;
    setProviderType(engine.provider_type);
    setBaseUrl(engine.base_url);
    setIsLocal(Boolean(engine.local));
    // Connection name auto-seed (family contract): the engine label,
    // numbered when taken — re-seeded while untouched or still auto-seeded.
    if (!nameDirty || engineOptions.some((option) => option.name === name)) {
      setName(defaultName(engine.name, existingNames));
      setNameDirty(false);
    }
  };

  const onNameChange = (value: string) => {
    setName(value);
    setNameDirty(true);
  };

  const canSave = name.trim().length > 0 && !saving;

  const submit = async () => {
    setSaving(true);
    setFormError(null);
    const apiScope = scope === 'global' ? 'SYSTEM' : scope === 'tenant' ? 'TENANT' : 'USER';
    const trimmedKey = apiKey.trim();
    try {
      if (editing && provider) {
        await updateProvider(provider.id, {
          name: name.trim(),
          api_base: baseUrl.trim(),
          is_active: isActive,
          is_local: isLocal,
          company_name: companyName.trim() || null,
          company_website: companyWebsite.trim() || null,
          company_country: country || null,
          ...(trimmedKey ? { api_key: trimmedKey } : {}),
        });
      } else {
        await createProvider({
          name: name.trim(),
          provider_type: providerType,
          api_base: baseUrl.trim(),
          is_active: isActive,
          is_local: isLocal,
          company_name: companyName.trim() || null,
          company_website: companyWebsite.trim() || null,
          company_country: country || null,
          scope: apiScope,
          ...(trimmedKey ? { api_key: trimmedKey } : {}),
          ...(scope === 'user' && userId ? { user_id: userId } : {}),
          ...(scope === 'tenant' && tenantId ? { tenant_id: tenantId } : {}),
        });
      }
      onClose();
    } catch (err: any) {
      setFormError(err?.message ?? 'Failed to save provider');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      onOpenChange={(open) => (!open ? onClose() : undefined)}
      title={
        editing ? t('settings.ai.edit_provider') : t('settings.ai.add_provider')
      }
      footer={
        <div className="flex items-center gap-2">
          <label className="mr-auto flex items-center gap-2 text-xs font-bold uppercase text-gray-500 dark:text-dark-muted">
            <input
              type="checkbox"
              checked={isActive}
              onChange={(e) => setIsActive(e.target.checked)}
            />
            {t('settings.ai.active')}
          </label>
          <Button variant="ghost" size="sm" onClick={onClose}>
            {t('settings.ai.cancel')}
          </Button>
          <Button size="sm" disabled={!canSave} onClick={() => void submit().catch(() => undefined)}>
            {saving ? (
              <Loader2 className="animate-spin" aria-hidden />
            ) : editing ? (
              <Save aria-hidden />
            ) : (
              <Plus aria-hidden />
            )}
            {editing ? t('settings.ai.save') : t('settings.ai.create')}
          </Button>
        </div>
      }
    >
      <div className="space-y-4">
        {editing && provider ? (
          <div className="space-y-1 text-sm">
            <span className="text-xs font-bold uppercase tracking-widest text-gray-400">
              {t('settings.ai.provider_type')}
            </span>
            <p className="rounded-md bg-gray-100 px-3 py-2 text-xs text-gray-500 dark:bg-dark-bg dark:text-dark-muted">
              {provider.provider_type === 'tesseract'
                ? t('settings.ai.type_tesseract')
                : t('settings.ai.type_openai')}
            </p>
          </div>
        ) : null}
        <ProviderForm
          name={name}
          onNameChange={onNameChange}
          baseUrl={baseUrl}
          onBaseUrlChange={setBaseUrl}
          apiKey={apiKey}
          onApiKeyChange={setApiKey}
          nameLabel={t('settings.ai.provider_name')}
          baseUrlLabel={t('settings.ai.base_url')}
          apiKeyLabel={t('settings.ai.api_key')}
          apiKeyHelp={
            editing ? t('settings.ai.api_key_keep_hint') : t('settings.ai.api_key_help')
          }
          hasStoredKey={editing && (provider?.has_api_key ?? Boolean(provider?.api_key))}
          storedKeyLabel={editing ? provider?.api_key ?? undefined : undefined}
          {...(!editing
            ? {
                presets: engineOptions,
                presetKey,
                onPresetChange: applyPreset,
                presetLabel: t('settings.ai.provider_type'),
                customPresetLabel: t('settings.ai.setup.custom'),
              }
            : {})}
          showLocationKind
          locationKind={isLocal ? 'local' : 'cloud'}
          onLocationKindChange={(kind) => setIsLocal(kind === 'local')}
          locationLabel={t('settings.ai.hosting')}
          localLabel={t('settings.ai.local_kind')}
          cloudLabel={t('settings.ai.cloud_kind')}
          showCountry
          country={country}
          onCountryChange={setCountry}
          countryLabel={t('settings.ai.country_label')}
          countryPlaceholder={t('settings.ai.country_placeholder')}
          countrySearchPlaceholder={t('settings.ai.country_search')}
          countryEmptyLabel={t('settings.ai.country_empty')}
          countryClearLabel={t('settings.ai.country_clear')}
          countryOptions={COUNTRIES.map((entry) => ({
            value: entry.code,
            label: `${entry.flag} ${countryDisplayName(entry.code, i18n.resolvedLanguage ?? 'en')}`,
          }))}
          error={formError ?? undefined}
        >
          <div className={PROVIDER_FORM_FIELDS}>
            <ComplianceFields
              companyName={companyName}
              onCompanyNameChange={setCompanyName}
              companyWebsite={companyWebsite}
              onCompanyWebsiteChange={setCompanyWebsite}
              typeLabel={t('settings.ai.company_name')}
            />
          </div>
        </ProviderForm>
      </div>
    </Modal>
  );
};
