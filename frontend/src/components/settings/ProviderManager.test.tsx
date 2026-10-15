import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ProviderManager } from './ProviderManager';
import type { AIProvider } from '../../api/aiConfig';

const providers: AIProvider[] = [
  {
    id: 'prov-1',
    name: 'OpenAI Main',
    scope: 'USER',
    provider_type: 'openai',
    api_base: 'https://api.openai.com/v1',
    api_key: '***sk-1a2b',
    has_api_key: true,
    is_active: true,
    is_local: false,
    company_name: 'OpenAI, Inc.',
    company_country: 'US',
    user_id: 'u1',
  },
];

const store = {
  providers,
  models: [{ id: 'm-1', provider_id: 'prov-1', name: 'GPT 4o', model_name: 'gpt-4o' }] as Array<
    Record<string, unknown>
  >,
  createProvider: vi.fn().mockResolvedValue(providers[0]),
  updateProvider: vi.fn().mockResolvedValue(providers[0]),
  deleteProvider: vi.fn().mockResolvedValue(undefined),
  fetchExternalModels: vi.fn().mockResolvedValue([{ id: 'gpt-4o' }, { id: 'gpt-4o-mini' }]),
  error: null,
  clearError: vi.fn(),
};
const showConfirmation = vi.fn();

vi.mock('../../store/slices/aiConfigSlice', () => ({
  useAIConfigStore: Object.assign(vi.fn(() => store), { getState: vi.fn() }),
}));

vi.mock('../../api/aiConfig', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/aiConfig')>();
  return {
    ...actual,
    aiConfigApi: {
      ...actual.aiConfigApi,
      listProviderPresets: vi
        .fn()
        .mockResolvedValue({ order: [], presets: {}, disabled: {} }),
    },
  };
});

vi.mock('../../store/slices/uiSlice', () => ({
  useUIStore: Object.assign(
    vi.fn((selector: (state: { showConfirmation: unknown }) => unknown) =>
      selector({ showConfirmation }),
    ),
    { getState: vi.fn() },
  ),
}));

const STRINGS: Record<string, string> = {
  'settings.ai.providers_hint': 'Manage the AI providers.',
  'settings.ai.add_provider': 'Add Provider',
  'settings.ai.edit_provider': 'Edit Provider',
  'settings.ai.delete_provider': 'Delete Provider',
  'settings.ai.delete_provider_confirm': 'Are you sure?',
  'settings.ai.connection': 'Connection',
  'settings.ai.test': 'Test',
  'settings.ai.test_ok': 'Connected',
  'settings.ai.test_failed': 'Failed',
  'settings.ai.models_count': '{{count}} models',
  'settings.ai.no_key': 'no key',
  'settings.ai.not_tested': 'Not tested',
  'settings.ai.key_set': 'key set',
  'settings.ai.actions_aria': 'Actions for {{name}}',
  'settings.ai.edit': 'Edit',
  'settings.ai.provider_name': 'Friendly Name',
  'settings.ai.base_url': 'API Base URL',
  'settings.ai.api_key': 'API Key',
  'settings.ai.api_key_help': 'Stored encrypted at rest.',
  'settings.ai.api_key_keep_hint': 'Stored encrypted at rest — leave empty to keep.',
  'settings.ai.hosting': 'Deployment Type',
  'settings.ai.local_kind': 'Local / On-Premise',
  'settings.ai.cloud_kind': 'Cloud / Managed Service',
  'settings.ai.country_label': 'Jurisdiction / Country',
  'settings.ai.country_placeholder': 'Select Country',
  'settings.ai.company_name': 'Company Name',
  'settings.ai.company_website': 'Company Website',
  'settings.ai.provider_type': 'Provider Type',
  'settings.ai.type_openai': 'OpenAI (LLM)',
  'settings.ai.type_tesseract': 'Tesseract (OCR)',
  'settings.ai.active': 'Active',
  'settings.ai.disabled': 'Disabled',
  'settings.ai.create': 'Create Provider',
  'settings.ai.scope_system': 'System',
  'settings.ai.scope_org': 'Org',
  'settings.ai.scope_personal': 'Personal',
  'settings.ai.no_providers': 'No providers configured yet.',
  'settings.ai.cancel': 'Cancel',
  'settings.ai.save': 'Save',
  'settings.ai.dismiss': 'Dismiss',
  'settings.ai.setup.title': 'Add a provider',
  'settings.ai.setup.modal_hint': 'Connect a provider with your own API key.',
  'settings.ai.setup.custom': 'Custom',
  'settings.ai.setup.automatically': 'Set up automatically',
};

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const raw = STRINGS[key] ?? key;
      if (!params) return raw;
      return Object.entries(params).reduce(
        (acc, [k, v]) => acc.split(`{{${k}}}`).join(String(v)),
        raw,
      );
    },
    i18n: { resolvedLanguage: 'en' },
  }),
}));

const openActionsMenu = async (name: string) => {
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: `Actions for ${name}` }));
  return user;
};

beforeEach(() => {
  vi.clearAllMocks();
  store.models = [{ id: 'm-1', provider_id: 'prov-1', name: 'GPT 4o', model_name: 'gpt-4o' }];
});

describe('ProviderManager', () => {
  it('renders provider cards with badges, the base-URL subtitle and the live model count', () => {
    render(<ProviderManager scope="user" userId="u1" />);
    expect(screen.getByText('OpenAI Main')).toBeInTheDocument();
    expect(screen.getByText('Personal')).toBeInTheDocument();
    expect(screen.getByText('Cloud / Managed Service')).toBeInTheDocument();
    // base URL: discrete subtitle (full text on its tooltip), plus the name tooltip
    expect(screen.getByText(/https:\/\/api\.openai\.com\/v1/)).toBeInTheDocument();
    expect(screen.getByText('OpenAI Main')).toHaveAttribute(
      'title',
      'https://api.openai.com/v1',
    );
    // keyed provider → no key chip (implied); raw key never leaks
    expect(screen.queryByText(/\*\*\*sk-1a2b/)).not.toBeInTheDocument();
    expect(screen.queryByTestId('provider-key-prov-1')).toBeNull();
    // live registered-model count from the store
    expect(screen.getByText('1 models')).toBeInTheDocument();
    // country badge keeps its flag + code and the country as tooltip
    expect(screen.getByTestId('provider-country-prov-1')).toHaveTextContent('🇺🇸 US');
  });

  it('tests the connection from the actions menu and reports the model count', async () => {
    render(<ProviderManager scope="user" userId="u1" />);
    await openActionsMenu('OpenAI Main');
    // no inline test button on the card — test lives only in the menu
    expect(screen.queryByRole('button', { name: 'Test' })).toBeNull();
    const item = await screen.findByRole('menuitem', { name: 'Test' });
    const user = userEvent.setup();
    await user.click(item);
    expect(store.fetchExternalModels).toHaveBeenCalledWith('prov-1');
    expect(await screen.findByText('Connected')).toBeInTheDocument();
    // the meta is the live registered-model count from the store
    expect(screen.getByText('1 models')).toBeInTheDocument();
  });

  it('disables the Test action while a test is running', async () => {
    render(<ProviderManager scope="user" userId="u1" />);
    let resolveTest: (value: unknown[]) => void = () => undefined;
    store.fetchExternalModels.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveTest = resolve;
        }),
    );
    await openActionsMenu('OpenAI Main');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Test' }));
    // the connection row flips to its testing state while the ping runs
    expect(await screen.findByRole('status')).toHaveTextContent('Test');
    // reopen the menu — Test is disabled and marked busy while pending
    fireEvent.keyDown(screen.getByRole('button', { name: 'Actions for OpenAI Main' }), {
      key: 'ArrowDown',
    });
    const item = await screen.findByRole('menuitem', { name: 'Test' }, { timeout: 2000 });
    expect(item).toHaveAttribute('aria-disabled', 'true');
    expect(item).toHaveAttribute('aria-busy', 'true');
    resolveTest([{ id: 'gpt-4o' }, { id: 'gpt-4o-mini' }]);
    expect(await screen.findByText('Connected')).toBeInTheDocument();
  });

  it('omits the Test action when no registered model exists', async () => {
    store.models = [];
    render(<ProviderManager scope="user" userId="u1" />);
    await openActionsMenu('OpenAI Main');
    expect(await screen.findByRole('menuitem', { name: 'Edit' })).toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: 'Test' })).toBeNull();
  });

  it('edits a provider through the actions menu and the ProviderForm dialog without touching the stored key', async () => {
    render(<ProviderManager scope="user" userId="u1" />);
    const user = await openActionsMenu('OpenAI Main');
    await user.click(await screen.findByRole('menuitem', { name: 'Edit' }));
    const dialog = screen.getByRole('dialog');
    const name = within(dialog).getByLabelText('Friendly Name');
    expect(name).toHaveValue('OpenAI Main');
    const key = within(dialog).getByLabelText(/API Key/);
    expect(key).toHaveAttribute('type', 'password');
    expect(key).toHaveValue('');
    await user.clear(name);
    await user.type(name, 'OpenAI Renamed');
    await user.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(store.updateProvider).toHaveBeenCalledWith(
        'prov-1',
        expect.objectContaining({ name: 'OpenAI Renamed' }),
      ),
    );
    expect(store.updateProvider.mock.calls[0][1]).not.toHaveProperty('api_key');
  });

  it('creates a provider through the setup modal custom tile in the current scope', async () => {
    const user = userEvent.setup();
    render(<ProviderManager scope="user" userId="u1" />);
    await user.click(screen.getByRole('button', { name: 'Add Provider' }));
    expect(await screen.findByText('Add a provider')).toBeInTheDocument();
    await user.click(screen.getByText('Custom'));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Friendly Name'), 'My Ollama');
    await user.click(within(dialog).getByRole('button', { name: 'Local / On-Premise' }));
    await user.click(within(dialog).getByRole('button', { name: 'Create Provider' }));
    await waitFor(() =>
      expect(store.createProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'My Ollama',
          scope: 'USER',
          user_id: 'u1',
          is_local: true,
        }),
      ),
    );
  });

  it('routes deletion through the actions menu and the app confirmation flow', async () => {
    render(<ProviderManager scope="user" userId="u1" />);
    const user = await openActionsMenu('OpenAI Main');
    await user.click(await screen.findByRole('menuitem', { name: 'Delete Provider' }));
    expect(showConfirmation).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Delete Provider', confirmVariant: 'danger' }),
    );
  });
});
