import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
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
  }),
}));

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ProviderManager', () => {
  it('renders provider cards with scope, hosting and masked key', () => {
    render(<ProviderManager scope="user" userId="u1" />);
    expect(screen.getByText('OpenAI Main')).toBeInTheDocument();
    expect(screen.getByText('Personal')).toBeInTheDocument();
    expect(screen.getByText('Cloud / Managed Service')).toBeInTheDocument();
    expect(screen.getByText(/https:\/\/api.openai.com\/v1 · \*\*\*sk-1a2b/)).toBeInTheDocument();
    expect(screen.getByText('🇺🇸')).toBeInTheDocument();
  });

  it('tests the connection inline and reports the model count', async () => {
    const user = userEvent.setup();
    render(<ProviderManager scope="user" userId="u1" />);
    await user.click(screen.getByRole('button', { name: 'Test' }));
    expect(store.fetchExternalModels).toHaveBeenCalledWith('prov-1');
    expect(await screen.findByText('2 models')).toBeInTheDocument();
    expect(screen.getByText('Connected')).toBeInTheDocument();
  });

  it('edits a provider through the ProviderForm dialog without touching the stored key', async () => {
    const user = userEvent.setup();
    render(<ProviderManager scope="user" userId="u1" />);
    await user.click(screen.getByRole('button', { name: 'Edit Provider' }));
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

  it('creates a provider in the current scope with transparency fields', async () => {
    const user = userEvent.setup();
    render(<ProviderManager scope="user" userId="u1" />);
    await user.click(screen.getByRole('button', { name: 'Add Provider' }));
    const dialog = screen.getByRole('dialog');
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

  it('routes deletion through the app confirmation flow', async () => {
    const user = userEvent.setup();
    render(<ProviderManager scope="user" userId="u1" />);
    await user.click(screen.getByRole('button', { name: 'Delete Provider' }));
    expect(showConfirmation).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Delete Provider', confirmVariant: 'danger' }),
    );
  });
});
