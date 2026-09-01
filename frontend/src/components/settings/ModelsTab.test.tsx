import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ModelsTab } from './ModelsTab';
import type { AIModel, AIProvider } from '../../api/aiConfig';

const providers: AIProvider[] = [
  {
    id: 'prov-1',
    name: 'OpenAI Main',
    scope: 'USER',
    provider_type: 'openai',
    api_base: 'https://api.openai.com/v1',
    is_active: true,
  },
];

const models: AIModel[] = [
  {
    id: 'm-1',
    provider_id: 'prov-1',
    name: 'GPT-4o Clinical',
    model_name: 'gpt-4o',
    description: 'Workhorse chat model',
    capabilities: ['text', 'vision'],
    is_active: true,
    max_tokens: 65536,
    temperature: 0.7,
  },
];

const createStore = (overrides: Record<string, unknown> = {}) => ({
  providers,
  models,
  createModel: vi.fn().mockResolvedValue(models[0]),
  updateModel: vi.fn().mockResolvedValue(models[0]),
  deleteModel: vi.fn().mockResolvedValue(undefined),
  fetchExternalModels: vi
    .fn()
    .mockResolvedValue([{ id: 'gpt-4o-mini' }, { id: 'whisper-1' }]),
  error: null,
  clearError: vi.fn(),
  ...overrides,
});

const store = createStore();
const showConfirmation = vi.fn();

vi.mock('../../store/slices/aiConfigSlice', () => ({
  useAIConfigStore: Object.assign(
    vi.fn(() => store),
    { getState: vi.fn() },
  ),
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
  'settings.ai.hint': 'Register the models you want to use.',
  'settings.ai.caps_text': 'Text',
  'settings.ai.caps_vision': 'Vision',
  'settings.ai.caps_audio_input': 'Audio Input',
  'settings.ai.caps_label': 'Capabilities',
  'settings.ai.caps_hint': 'Capabilities decide which tasks can use this model.',
  'settings.ai.add_model': 'Add model',
  'settings.ai.add_all': 'Add all',
  'settings.ai.edit_model': 'Edit model',
  'settings.ai.save': 'Save',
  'settings.ai.cancel': 'Cancel',
  'settings.ai.retry': 'Retry',
  'settings.ai.manual_id': 'Enter the model id manually',
  'settings.ai.select_model': 'Model',
  'settings.ai.search_models': 'Search models…',
  'settings.ai.temperature': 'Temperature',
  'settings.ai.max_tokens': 'Max tokens',
  'settings.ai.display_label': 'Display label',
  'settings.ai.description': 'Description',
  'settings.ai.description_placeholder': 'What is this model used for?',
  'settings.ai.enabled': 'Enabled',
  'settings.ai.remove': 'Remove',
  'settings.ai.missing': 'Missing',
  'settings.ai.empty_provider': 'No models yet.',
  'settings.ai.remote_empty': 'The provider listed no models.',
  'settings.ai.remote_loading': 'Loading models…',
  'settings.ai.custom_option': 'Custom…',
  'settings.ai.providers_empty': 'No providers yet.',
  'settings.ai.id_required': 'The model id is required.',
  'settings.ai.delete_model': 'Delete Model',
  'settings.ai.delete_model_confirm': 'Are you sure?',
  'settings.ai.dismiss': 'Dismiss',
};

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => STRINGS[key] ?? key }),
}));

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ModelsTab', () => {
  it('renders the registry with provider card, model row and capability badges', async () => {
    render(<ModelsTab />);
    expect(await screen.findByText('OpenAI Main')).toBeInTheDocument();
    expect(screen.getByText('gpt-4o')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Enabled — gpt-4o' })).toBeChecked();
    expect(screen.getByText('Text')).toBeInTheDocument();
    expect(screen.getByText('Vision')).toBeInTheDocument();
  });

  it('fetches the remote catalog for the expanded provider', async () => {
    render(<ModelsTab />);
    await waitFor(() => expect(store.fetchExternalModels).toHaveBeenCalledWith('prov-1'));
  });

  it('adds a model from the remote catalog with inferred capabilities', async () => {
    const user = userEvent.setup();
    render(<ModelsTab />);
    await user.click(await screen.findByRole('button', { name: 'Add model' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('combobox', { name: 'Model' }));
    await user.click(screen.getByRole('option', { name: 'whisper-1' }));
    expect(within(dialog).getByLabelText('Display label')).toHaveValue('Whisper 1');
    await user.click(within(dialog).getByRole('button', { name: 'Add model' }));
    await waitFor(() =>
      expect(store.createModel).toHaveBeenCalledWith(
        'prov-1',
        expect.objectContaining({
          name: 'Whisper 1',
          model_name: 'whisper-1',
          capabilities: ['audio_input'],
          is_active: true,
        }),
      ),
    );
  });

  it('maps registry patches onto the health model payload', async () => {
    const user = userEvent.setup();
    render(<ModelsTab />);
    await user.click(await screen.findByRole('button', { name: 'Edit model gpt-4o' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText('Description')).toHaveValue('Workhorse chat model');
    await user.clear(within(dialog).getByLabelText('Temperature'));
    await user.type(within(dialog).getByLabelText('Temperature'), '0.2');
    await user.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(store.updateModel).toHaveBeenCalledWith(
        'm-1',
        expect.objectContaining({ temperature: 0.2 }),
      ),
    );
  });

  it('persists the enable toggle', async () => {
    const user = userEvent.setup();
    render(<ModelsTab />);
    await user.click(await screen.findByRole('checkbox', { name: 'Enabled — gpt-4o' }));
    await waitFor(() =>
      expect(store.updateModel).toHaveBeenCalledWith('m-1', { is_active: false }),
    );
  });

  it('routes deletion through the app confirmation flow', async () => {
    const user = userEvent.setup();
    render(<ModelsTab />);
    await user.click(await screen.findByRole('button', { name: 'Remove gpt-4o' }));
    expect(showConfirmation).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Delete Model', confirmVariant: 'danger' }),
    );
  });
});
