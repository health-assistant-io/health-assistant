import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { House, Loader2, Pencil, Plus, Save, Trash2 } from 'lucide-react';
import {
  ConnectionTestRow,
  ProviderForm,
  type ConnectionTestStatus,
} from '@neuronection/assistant-ui';
import { useAIConfigStore } from '../../store/slices/aiConfigSlice';
import { AIProvider } from '../../api/aiConfig';
import { useUIStore } from '../../store/slices/uiSlice';
import { Button } from '../ui/Button';
import { Card, CardContent } from '../ui/Card';
import { Modal } from '../ui/Modal';
import { COUNTRIES } from '../../utils/countryUtils';

const COUNTRY_OPTIONS = COUNTRIES.map((c) => ({
  value: c.code,
  label: `${c.flag} ${c.name}`,
}));

const countryFlag = (code: string): string =>
  COUNTRIES.find((c) => c.code === code)?.flag ?? '';

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
    deleteProvider,
    fetchExternalModels,
    error,
    clearError,
  } = useAIConfigStore();

  const [dialog, setDialog] = useState<{ provider: AIProvider | null } | null>(null);
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
        <Button size="sm" onClick={() => setDialog({ provider: null })}>
          <Plus aria-hidden />
          {t('settings.ai.add_provider')}
        </Button>
      </div>

      {providers.map((provider) => {
        const test = tests[provider.id];
        const status: ConnectionTestStatus =
          test?.status ?? 'idle';
        return (
          <Card key={provider.id}>
            <CardContent className="flex items-start gap-3 p-4">
              <div className="min-w-0 flex-1">
                <p className="flex flex-wrap items-center gap-2 truncate text-sm font-bold text-gray-900 dark:text-dark-text">
                  {provider.name}
                  <span className="text-muted-foreground text-xs font-medium">
                    · {provider.provider_type}
                  </span>
                  {provider.is_local ? (
                    <span className="inline-flex items-center gap-1 rounded-full bg-green-50 px-2 py-0.5 text-[10px] font-bold uppercase text-green-600 dark:bg-green-900/30">
                      <House className="h-3 w-3" aria-hidden />
                      {t('settings.ai.local_kind')}
                    </span>
                  ) : (
                    <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-bold uppercase text-blue-600 dark:bg-blue-900/30">
                      {t('settings.ai.cloud_kind')}
                    </span>
                  )}
                  {!provider.user_id && !provider.tenant_id ? (
                    <span className="rounded-full bg-purple-100 px-2 py-0.5 text-[10px] font-bold uppercase text-purple-600 dark:bg-purple-900/30 dark:text-purple-400">
                      {t('settings.ai.scope_system')}
                    </span>
                  ) : provider.tenant_id && !provider.user_id ? (
                    <span className="rounded-full bg-blue-100 px-2 py-0.5 text-[10px] font-bold uppercase text-blue-600 dark:bg-blue-900/30 dark:text-blue-400">
                      {t('settings.ai.scope_org')}
                    </span>
                  ) : (
                    <span className="rounded-full bg-green-100 px-2 py-0.5 text-[10px] font-bold uppercase text-green-600 dark:bg-green-900/30 dark:text-green-400">
                      {t('settings.ai.scope_personal')}
                    </span>
                  )}
                  {!provider.is_active ? (
                    <span className="rounded border border-gray-200 px-1.5 py-0.5 text-[10px] font-bold uppercase text-gray-500 dark:border-dark-border dark:text-dark-muted">
                      {t('settings.ai.disabled')}
                    </span>
                  ) : null}
                </p>
                <p className="truncate font-mono text-xs text-gray-400">
                  {provider.api_base}
                  {' · '}
                  {provider.has_api_key ? provider.api_key : t('settings.ai.no_key')}
                  {provider.company_country ? (
                    <span className="ml-2" title={provider.company_country}>
                      {countryFlag(provider.company_country)}
                    </span>
                  ) : null}
                </p>
                <ConnectionTestRow
                  variant="inline"
                  className="mt-1"
                  label={t('settings.ai.connection')}
                  status={status}
                  errorMessage={test?.error ?? null}
                  meta={
                    test?.modelCount != null
                      ? t('settings.ai.models_count', { count: test.modelCount })
                      : undefined
                  }
                  testLabel={t('settings.ai.test')}
                  okLabel={t('settings.ai.test_ok')}
                  failLabel={t('settings.ai.test_failed')}
                  onTest={() => void runTest(provider).catch(() => undefined)}
                />
              </div>
              <Button
                variant="ghost"
                size="icon"
                title={t('settings.ai.edit_provider')}
                onClick={() => setDialog({ provider })}
              >
                <Pencil className="h-4 w-4" aria-hidden />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                title={t('settings.ai.delete_provider')}
                onClick={() => handleDelete(provider)}
              >
                <Trash2 className="h-4 w-4" aria-hidden />
              </Button>
            </CardContent>
          </Card>
        );
      })}

      {providers.length === 0 ? (
        <p className="py-6 text-center text-sm text-gray-500 dark:text-gray-400">
          {t('settings.ai.no_providers')}
        </p>
      ) : null}

      {error ? (
        <div className="flex items-center justify-between rounded-lg border border-red-200 bg-red-100 p-3 dark:border-red-900/50 dark:bg-red-900/30">
          <span className="text-sm text-red-700 dark:text-red-300">{error}</span>
          <button onClick={clearError} className="px-2 py-1 text-xs font-bold underline">
            {t('settings.ai.dismiss')}
          </button>
        </div>
      ) : null}

      {dialog ? (
        <ProviderFormDialog
          provider={dialog.provider}
          scope={scope}
          userId={userId}
          tenantId={tenantId}
          onClose={() => setDialog(null)}
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
          className="w-full rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm shadow-sm outline-none transition-all focus:ring-2 focus:ring-blue-500/20 dark:border-dark-border dark:bg-dark-surface dark:text-dark-text"
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
          className="w-full rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm shadow-sm outline-none transition-all focus:ring-2 focus:ring-blue-500/20 dark:border-dark-border dark:bg-dark-surface dark:text-dark-text"
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
  onClose: () => void;
}> = ({ provider, scope, userId, tenantId, onClose }) => {
  const { t } = useTranslation();
  const { createProvider, updateProvider } = useAIConfigStore();
  const editing = provider !== null;

  const [name, setName] = useState(provider?.name ?? '');
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
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

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
        ) : (
          <div className={PROVIDER_FORM_FIELDS}>
            <label className="block space-y-1 text-sm">
              <span className="text-xs font-bold uppercase tracking-widest text-gray-400">
                {t('settings.ai.provider_type')}
              </span>
              <select
                value={providerType}
                onChange={(e) => setProviderType(e.target.value)}
                className="w-full rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm shadow-sm outline-none transition-all focus:ring-2 focus:ring-blue-500/20 dark:border-dark-border dark:bg-dark-surface dark:text-dark-text"
              >
                <option value="openai">{t('settings.ai.type_openai')}</option>
                <option value="tesseract">{t('settings.ai.type_tesseract')}</option>
              </select>
            </label>
          </div>
        )}
        <ProviderForm
          name={name}
          onNameChange={setName}
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
          countryOptions={COUNTRY_OPTIONS}
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
        {formError ? (
          <p role="alert" className="text-xs text-red-600 dark:text-red-400">
            {formError}
          </p>
        ) : null}
      </div>
    </Modal>
  );
};
