import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ProviderSetupModal } from './ProviderSetupModal';

const setupProvider = vi.fn();
const setProviderDefault = vi.fn();
const createProvider = vi.fn();

const storeState = {
  setupProvider,
  setProviderDefault,
  createProvider,
  configSummary: {
    models: [
      {
        id: 'm1',
        provider_id: 'prov-1',
        name: 'GPT-5.6 Terra',
        model_name: 'gpt-5.6-terra',
        capabilities: ['text', 'vision', 'tools'],
        is_active: true,
      },
      {
        id: 'm2',
        provider_id: 'prov-1',
        name: 'whisper-1',
        model_name: 'whisper-1',
        capabilities: ['stt'],
        is_active: true,
      },
    ],
    // Live "now:" context for the review's assignment rows — the text
    // default is taken, vision/transcription are unset.
    default: {
      task_type: 'default',
      model: { id: 'm0', provider_id: 'prov-0', name: 'GPT-4o legacy', model_name: 'gpt-4o-legacy', is_active: true },
    },
  },
};

vi.mock('../../../store/slices/aiConfigSlice', () => ({
  useAIConfigStore: Object.assign(
    vi.fn((selector?: (s: any) => any) => (selector ? selector(storeState) : storeState)),
    { getState: vi.fn(() => storeState) },
  ),
}));

const listProviderPresets = vi.fn();
const getProviderWithModels = vi.fn();

vi.mock('../../../api/aiConfig', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/aiConfig')>();
  return {
    ...actual,
    aiConfigApi: {
      ...actual.aiConfigApi,
      listProviderPresets: () => listProviderPresets(),
      getProviderWithModels: () => getProviderWithModels(),
    },
  };
});

const PRESETS = {
  order: ['openai', 'openrouter', 'ollama'],
  presets: {
    openai: {
      key: 'openai',
      name: 'OpenAI',
      provider_type: 'openai',
      wire_type: 'openai_compatible',
      base_url: 'https://api.openai.com/v1',
      fixed_base: false,
      local: false,
      key_url: 'https://platform.openai.com/api-keys',
      preferred_model: { id: 'gpt-5.6-terra', name: 'GPT-5.6 Terra', caps: ['text', 'tools', 'vision'] },
      curated_models: ['gpt-5.6-terra', 'whisper-1'],
      stt_model: 'whisper-1',
      steps: ['Open {url} and sign in.', 'Paste it below and connect.'],
      free_tier_note: null,
    },
    openrouter: {
      key: 'openrouter',
      name: 'OpenRouter',
      provider_type: 'openai',
      wire_type: 'openai_compatible',
      base_url: 'https://openrouter.ai/api/v1',
      fixed_base: true,
      local: false,
      key_url: null,
      preferred_model: null,
      curated_models: ['openrouter/auto'],
      stt_model: null,
      decision_models: [
        {
          id: 'openrouter.jev-latest',
          name: 'Jev via OpenRouter (latest)',
          transport: 'systemone',
          caps: ['tool-dispatch', 'boolean-gate', 'choice', 'score'],
          provisional: true,
        },
      ],
      decision_default: 'openrouter.jev-latest',
      provisional: null,
      steps: null,
      free_tier_note: null,
    },
    ollama: {
      key: 'ollama',
      name: 'Ollama (local)',
      provider_type: 'openai',
      wire_type: 'openai_compatible',
      base_url: 'http://localhost:11434/v1',
      fixed_base: false,
      local: true,
      key_url: null,
      preferred_model: null,
      curated_models: null,
      stt_model: null,
      steps: null,
      free_tier_note: null,
    },
  },
  disabled: { gemini: 'not wired', anthropic: 'not wired' },
};

const SETUP_OK = {
  provider: {
    id: 'prov-1',
    name: 'OpenAI',
    scope: 'USER',
    provider_type: 'openai',
    api_base: 'https://api.openai.com/v1',
    preset_key: 'openai',
    is_active: true,
    is_local: false,
  },
  catalog_count: 2,
  curated_missed: false,
  assigned_chat_model: 'gpt-5.6-terra',
  assigned_vision_model: 'gpt-5.6-terra',
  assigned_stt_model: 'whisper-1',
};

const STRINGS: Record<string, string> = {
  'settings.ai.hosting': 'Deployment Type',
  'settings.ai.local_kind': 'Local / On-Premise',
  'settings.ai.cloud_kind': 'Cloud / Managed Service',
  'settings.ai.country_label': 'Jurisdiction / Country',
  'settings.ai.country_placeholder': 'Select Country',
  'settings.ai.api_key': 'API Key',
  'settings.ai.base_url': 'API Base URL',
  'settings.ai.close': 'Close',
  'settings.ai.setup.title': 'Add a provider',
  'settings.ai.setup.modal_hint': 'Connect a provider with your own API key.',
  'settings.ai.setup.form_title': 'Set up {{name}}',
  'settings.ai.setup.done': '{{name}} is connected.',
  'settings.ai.setup.models_persisted': '{{count}} curated models were added.',
  'settings.ai.setup.curated_missed': 'None of the curated models were found.',
  'settings.ai.setup.no_defaults': 'No models were persisted.',
  'settings.ai.setup.default_models': 'Default models',
  'settings.ai.setup.slot_chat': 'Text / chat',
  'settings.ai.setup.slot_vision': 'Vision',
  'settings.ai.setup.slot_stt': 'Transcription',
  'settings.ai.task_decision_default': 'Default decision model',
  'settings.ai.slot_decision_tool_dispatch': 'Tool dispatch',
  'settings.ai.slot_decision_boolean_gate': 'Boolean gate',
  'settings.ai.slot_decision_choice': 'Choice',
  'settings.ai.slot_decision_score': 'Score',
  'settings.ai.setup.get_key': 'Get an API key',
  'settings.ai.setup.connection_name': 'Connection name',
  'settings.ai.setup.no_key_needed': 'No API key needed for this local provider.',
  'settings.ai.setup.advanced': 'Advanced',
  'settings.ai.setup.base_edit_hint': 'Automatic setup uses the preset URL — edited bases connect manually.',
  'settings.ai.setup.manually': 'Set up manually',
  'settings.ai.setup.manually_hint': 'Create the provider without fetching models or changing defaults.',
  'settings.ai.setup.choose_another': 'Choose another provider',
  'settings.ai.setup.automatically': 'Set up automatically',
  'settings.ai.setup.custom': 'Custom',
  'settings.ai.setup.copy_step': 'Copy step',
  'settings.ai.setup.errors.invalid_key': 'That API key was rejected.',
  'settings.ai.setup.errors.suspected_vendor': 'Heads-up: this key looks like a {{vendor}} key.',
  'common.back': 'Back',
  'settings.ai.setup.step.progress': 'Setup progress',
  'settings.ai.setup.step.connect': 'Connect provider',
  'settings.ai.setup.step.defaults': 'Choose defaults',
  'settings.ai.setup.review.intro':
    '{{name}} connected — {{count}} models found. Choose the models to add, then which empty defaults they fill.',
  'settings.ai.setup.review.models_title': 'Models to add',
  'settings.ai.setup.review.models_hint':
    'Selected models are added to your catalog and become assignable everywhere.',
  'settings.ai.setup.review.models_footnote': 'Only the selected models are added — you can add more anytime.',
  'settings.ai.setup.review.assignments_title': 'Default assignments',
  'settings.ai.setup.review.assignments_hint':
    'Each switch fills that empty default with the shown model — existing assignments are never touched.',
  'settings.ai.setup.review.fills': 'fills: {{model}}',
  'settings.ai.setup.review.no_candidate': 'no model selected',
  'settings.ai.setup.review.now': 'now: {{model}}',
  'settings.ai.setup.review.unset': 'not set',
  'settings.ai.setup.review.apply': 'Apply',
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

function renderModal(onManual = vi.fn()) {
  const onClose = vi.fn();
  render(<ProviderSetupModal open onClose={onClose} onManual={onManual} />);
  return { onClose, onManual };
}

beforeEach(() => {
  vi.clearAllMocks();
  listProviderPresets.mockResolvedValue(PRESETS);
  getProviderWithModels.mockResolvedValue({
    models: [
      {
        id: 'm1',
        provider_id: 'prov-1',
        name: 'GPT-5.6 Terra',
        model_name: 'gpt-5.6-terra',
        capabilities: ['text', 'vision', 'tools'],
        is_active: true,
      },
      {
        id: 'm2',
        provider_id: 'prov-1',
        name: 'whisper-1',
        model_name: 'whisper-1',
        capabilities: ['stt'],
        is_active: true,
      },
    ],
  });
});

describe('ProviderSetupModal', () => {
  it('opens on the tile grid in the canonical family order with a custom tile', async () => {
    renderModal();
    const openai = await screen.findByText('OpenAI');
    const ollama = screen.getByText('Ollama (local)');
    expect(openai.compareDocumentPosition(ollama) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText('Custom')).toBeInTheDocument();
    // overlay-disabled presets never render tiles
    expect(screen.queryByText(/gemini/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/anthropic/i)).not.toBeInTheDocument();
  });

  it('preset form shows steps with copy buttons, key link, and an editable advanced base URL', async () => {
    const user = userEvent.setup();
    renderModal();
    await user.click(await screen.findByText('OpenAI'));
    expect(
      await screen.findByText(/open https:\/\/platform\.openai\.com\/api-keys and sign in\./i),
    ).toBeInTheDocument();
    // every checklist step carries a copy affordance
    expect(screen.getAllByRole('button', { name: 'Copy step' })).toHaveLength(2);
    expect(screen.getByRole('link', { name: 'Get an API key' })).toHaveAttribute(
      'href',
      'https://platform.openai.com/api-keys',
    );
    await user.click(screen.getByText('Advanced'));
    const base = screen.getByDisplayValue('https://api.openai.com/v1');
    expect(base).not.toHaveAttribute('disabled');
  });

  it('set up automatically: bind-less connect → review → apply → done (two-step wizard)', async () => {
    const user = userEvent.setup();
    setupProvider.mockResolvedValue(SETUP_OK);
    renderModal();
    await user.click(await screen.findByText('OpenAI'));
    await user.type(await screen.findByLabelText('API Key'), '  sk-test  ');
    await user.click(screen.getByRole('button', { name: 'Set up automatically' }));

    // connect is bind-less — the review step decides what registers/binds
    await waitFor(() =>
      expect(setupProvider).toHaveBeenCalledWith('openai', {
        api_key: 'sk-test',
        name: null,
        scope: 'USER',
        options: { bind_chat: false, bind_vision: false, bind_stt: false, bind_decision: false },
      }),
    );

    // step 2 is explicit: the wizard header names it, the assignments
    // section carries now/fills context per task row
    expect(await screen.findByText('Choose defaults')).toBeInTheDocument();
    expect(screen.getByText('Default assignments')).toBeInTheDocument();
    // terra serves chat and vision — both rows name it; the live text
    // default rides the chat row's now-context
    expect(await screen.findAllByText(/fills: gpt-5\.6-terra/)).toHaveLength(2);
    expect(screen.getByText(/now: gpt-4o-legacy · fills: gpt-5\.6-terra/)).toBeInTheDocument();
    // whisper keeps its own card → its own candidate
    expect(screen.getByText(/fills: whisper-1/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() =>
      expect(setupProvider).toHaveBeenLastCalledWith(
        'openai',
        expect.objectContaining({
          api_key: 'sk-test',
          options: expect.objectContaining({
            curated_ids: ['gpt-5.6-terra', 'whisper-1'],
            bind_chat: true,
            bind_vision: true,
            bind_stt: true,
            bind_decision: false,
          }),
        }),
      ),
    );

    // done: the success panel with its per-slot pickers
    expect(await screen.findByText('OpenAI is connected.')).toBeInTheDocument();
    expect(screen.getByText('2 curated models were added.')).toBeInTheDocument();
    expect(screen.getByText('Default models')).toBeInTheDocument();
    expect(screen.getByText('Text / chat')).toBeInTheDocument();
    expect(screen.getByText('Transcription')).toBeInTheDocument();
  });

  it('classified errors route through the §15 i18n enum with the suspectedVendor hint', async () => {
    const user = userEvent.setup();
    setupProvider.mockRejectedValue({
      response: {
        data: {
          detail: { code: 'invalid_key', suspected_vendor: 'openrouter', message: 'bad key' },
        },
      },
    });
    renderModal();
    await user.click(await screen.findByText('OpenAI'));
    await user.type(await screen.findByLabelText('API Key'), 'sk-or-v1-abc');
    await user.click(screen.getByRole('button', { name: 'Set up automatically' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'That API key was rejected.',
    );
    expect(screen.getByText('Heads-up: this key looks like a openrouter key.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Choose another provider' })).toBeInTheDocument();
  });

  it('success panel renders decision rows bound to the provider decision models (plan 26)', async () => {
    const user = userEvent.setup();
    setupProvider.mockResolvedValue({
      ...SETUP_OK,
      // ADR-0036 §3: setup binds the `decision` default slot only.
      assigned_decision_slots: { decision: 'openrouter.jev-latest' },
    });
    getProviderWithModels.mockResolvedValue({
      models: [
        {
          id: 'm1',
          provider_id: 'prov-1',
          name: 'GPT-5.6 Terra',
          model_name: 'gpt-5.6-terra',
          capabilities: ['text', 'vision', 'tools'],
          is_active: true,
        },
        {
          id: 'm-jev',
          provider_id: 'prov-1',
          name: 'Jev via OpenRouter (latest)',
          model_name: 'openrouter.jev-latest',
          capabilities: ['decision'],
          decision_transport: 'systemone',
          is_active: true,
        },
      ],
    });
    renderModal();
    await user.click(await screen.findByText('OpenAI'));
    await user.type(await screen.findByLabelText('API Key'), 'sk-or-v1-key');
    await user.click(screen.getByRole('button', { name: 'Set up automatically' }));
    // the openai preset declares no decision models → no decision review row
    expect(await screen.findByText('Default assignments')).toBeInTheDocument();
    expect(screen.queryByText('Default decision model')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Apply' }));

    expect(await screen.findByText('OpenAI is connected.')).toBeInTheDocument();
    // ADR-0036 §3: ONE decision review row — the default decision model;
    // the kind slots inherit (plan 29 / ADR-0036).
        const decisionReviewRow = screen.getByText('Default decision model').closest('div')!;
    // The picker resolves the bound model's display name (D4: kind rows inherit).
    expect(within(decisionReviewRow).getByText('Jev via OpenRouter (latest)')).toBeInTheDocument();
    for (const slot of ['Tool dispatch', 'Boolean gate', 'Choice', 'Score']) {
      expect(screen.queryByText(slot)).not.toBeInTheDocument();
    }
  });

  it('setup review offers the decision default for decision-capable presets (ADR-0036)', async () => {
    const user = userEvent.setup();
    setupProvider.mockResolvedValue({
      ...SETUP_OK,
      provider: { ...SETUP_OK.provider, name: 'OpenRouter', preset_key: 'openrouter' },
      catalog_count: 1,
      assigned_chat_model: null,
      assigned_vision_model: null,
      assigned_stt_model: null,
      assigned_decision_slots: null,
    });
    getProviderWithModels.mockResolvedValue({
      models: [
        {
          id: 'm-auto',
          provider_id: 'prov-1',
          name: 'Auto (best match)',
          model_name: 'openrouter/auto',
          capabilities: ['text'],
          is_active: true,
        },
        {
          id: 'm-jev',
          provider_id: 'prov-1',
          name: 'Jev via OpenRouter (latest)',
          model_name: 'openrouter.jev-latest',
          capabilities: ['decision'],
          decision_transport: 'systemone',
          is_active: true,
        },
      ],
    });
    renderModal();
    await user.click(await screen.findByText('OpenRouter'));
    await user.type(await screen.findByLabelText('API Key'), 'sk-or-v1-key');
    await user.click(screen.getByRole('button', { name: 'Set up automatically' }));

    // review: the decision default row names the model it would bind
    // (openrouter declares decision models; the Jev card never rides the
    // curated list, so the sub-line is where it must surface)
    expect(await screen.findByText('Choose defaults')).toBeInTheDocument();
    expect(screen.getByText('Default decision model')).toBeInTheDocument();
    expect(
      (await screen.findAllByText(/fills: Jev via OpenRouter \(latest\)/)).length,
    ).toBeGreaterThan(0);
    // the auto card is text-only → the vision row has no candidate and disables
    expect(screen.getByText(/fills: openrouter\/auto/)).toBeInTheDocument();
    expect(screen.getByText('no model selected')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() =>
      expect(setupProvider).toHaveBeenLastCalledWith(
        'openrouter',
        expect.objectContaining({
          options: expect.objectContaining({
            curated_ids: ['openrouter/auto'],
            bind_decision: true,
          }),
        }),
      ),
    );
    // done: the registered decision model feeds the default-decision picker
    expect(await screen.findByText('OpenRouter is connected.')).toBeInTheDocument();
    expect(screen.getByText('Default decision model')).toBeInTheDocument();
  });

  it('local presets hide the key field and the footer can go back to the tiles', async () => {
    const user = userEvent.setup();
    renderModal();
    await user.click(await screen.findByText('Ollama (local)'));
    expect(screen.queryByLabelText('API Key')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Choose another provider' }));
    expect(await screen.findByText('OpenAI')).toBeInTheDocument();
  });

  it('custom tile routes to the manual form and closes the modal', async () => {
    const user = userEvent.setup();
    const { onClose, onManual } = renderModal();
    await user.click(await screen.findByText('Custom'));
    expect(onManual).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it('editing the base URL reroutes automatic setup through the manual create path', async () => {
    const user = userEvent.setup();
    const { onClose, onManual } = renderModal();
    createProvider.mockResolvedValue({ id: 'prov-9' });
    await user.click(await screen.findByText('OpenAI'));
    await user.click(screen.getByText('Advanced'));
    const baseInput = screen.getByLabelText('API Base URL');
    await user.clear(baseInput);
    await user.type(baseInput, 'https://relay.test/v1');

    // edited preset → the automatic action saves through the manual form
    await user.type(await screen.findByLabelText('API Key'), 'sk-test');
    await user.click(screen.getByRole('button', { name: 'Set up automatically' }));

    await waitFor(() =>
      expect(createProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'OpenAI',
          provider_type: 'openai',
          api_base: 'https://relay.test/v1',
          api_key: 'sk-test',
          scope: 'USER',
        }),
      ),
    );
    expect(setupProvider).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
    expect(onManual).not.toHaveBeenCalled();
  });

  it('set up manually creates the provider without fetching models or binding defaults', async () => {
    const user = userEvent.setup();
    const { onClose } = renderModal();
    createProvider.mockResolvedValue({ id: 'prov-9' });
    await user.click(await screen.findByText('OpenAI'));
    await user.type(await screen.findByLabelText('API Key'), 'sk-manual');
    await user.click(screen.getByRole('button', { name: 'Set up manually' }));

    await waitFor(() =>
      expect(createProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'OpenAI',
          provider_type: 'openai',
          api_base: 'https://api.openai.com/v1',
          api_key: 'sk-manual',
          scope: 'USER',
        }),
      ),
    );
    expect(setupProvider).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });
});
