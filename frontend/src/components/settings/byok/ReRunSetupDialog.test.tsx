import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ReRunSetupDialog } from './ReRunSetupDialog';
import type { AIProvider, AIConfigSummary } from '../../../api/aiConfig';

const setupProvider = vi.fn().mockResolvedValue({});

const storeState = {
  setupProvider,
  configSummary: null as AIConfigSummary | null,
};

vi.mock('../../../store/slices/aiConfigSlice', () => ({
  useAIConfigStore: Object.assign(
    vi.fn((selector?: (s: any) => any) => (selector ? selector(storeState) : storeState)),
    { getState: vi.fn(() => storeState) },
  ),
}));

const listProviderPresets = vi.fn();

vi.mock('../../../api/aiConfig', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/aiConfig')>();
  return {
    ...actual,
    aiConfigApi: {
      ...actual.aiConfigApi,
      listProviderPresets: () => listProviderPresets(),
    },
  };
});

const PROVIDER: AIProvider = {
  id: 'prov-1',
  name: 'OpenRouter',
  scope: 'USER',
  provider_type: 'openai',
  api_base: 'https://openrouter.ai/api/v1',
  preset_key: 'openrouter',
  is_active: true,
  is_local: false,
};

const OPENROUTER_PRESET = {
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
};

const OPENAI_PRESET = {
  ...OPENROUTER_PRESET,
  key: 'openai',
  name: 'OpenAI',
  decision_models: null,
  decision_default: null,
};

const STRINGS: Record<string, string> = {
  'settings.ai.setup.automatically': 'Set up automatically',
  'settings.ai.setup.review.key_note':
    '{{name}} will be re-checked against the vendor catalog. Your stored key is reused — it is never displayed.',
  'settings.ai.setup.review.models_label': 'Curated models',
  'settings.ai.setup.review.fill_text': 'Fill text/chat default',
  'settings.ai.setup.review.fill_vision': 'Fill vision default',
  'settings.ai.setup.review.fill_decision': 'Fill empty decision slots',
  'settings.ai.setup.review.decision_now': 'now: {{count}} of {{total}} slots filled',
  'settings.ai.setup.review.now': 'now: {{model}}',
  'settings.ai.setup.review.unset': 'not set',
  'settings.ai.setup.review.footnote':
    'Setup only fills empty slots — it never removes your models or overrides live assignments.',
  'settings.ai.setup.review.apply': 'Apply',
  'settings.ai.cancel': 'Cancel',
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

function renderDialog(provider: AIProvider = PROVIDER) {
  const onClose = vi.fn();
  render(<ReRunSetupDialog provider={provider} onClose={onClose} />);
  return { onClose };
}

beforeEach(() => {
  vi.clearAllMocks();
  setupProvider.mockResolvedValue({});
});

describe('ReRunSetupDialog (five-bind review body)', () => {
  it('offers the decision switch when the preset declares decision models and sends bind_decision', async () => {
    listProviderPresets.mockResolvedValue({
      order: ['openrouter'],
      presets: { openrouter: OPENROUTER_PRESET },
      disabled: {},
    });
    const user = userEvent.setup();
    const { onClose } = renderDialog();

    const decisionSwitch = await screen.findByRole('switch', { name: /Fill empty decision slots/ });
    expect(decisionSwitch).toBeChecked();
    expect(screen.getByText('now: 0 of 4 slots filled')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() =>
      expect(setupProvider).toHaveBeenCalledWith(
        'openrouter',
        expect.objectContaining({
          options: expect.objectContaining({
            bind_chat: true,
            bind_vision: true,
            bind_stt: true,
            bind_decision: true,
          }),
        }),
      ),
    );
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('omits the decision switch for presets without decision models and sends bind_decision: false', async () => {
    listProviderPresets.mockResolvedValue({
      order: ['openai'],
      presets: { openai: OPENAI_PRESET },
      disabled: {},
    });
    const user = userEvent.setup();
    renderDialog({ ...PROVIDER, preset_key: 'openai', name: 'OpenAI' });

    await screen.findByText('Fill text/chat default');
    expect(
      screen.queryByRole('switch', { name: /Fill empty decision slots/ }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() =>
      expect(setupProvider).toHaveBeenCalledWith(
        'openai',
        expect.objectContaining({
          options: expect.objectContaining({ bind_decision: false }),
        }),
      ),
    );
  });

  it('respects an explicit opt-out of the decision fill', async () => {
    listProviderPresets.mockResolvedValue({
      order: ['openrouter'],
      presets: { openrouter: OPENROUTER_PRESET },
      disabled: {},
    });
    const user = userEvent.setup();
    renderDialog();

    const decisionSwitch = await screen.findByRole('switch', { name: /Fill empty decision slots/ });
    await user.click(decisionSwitch);
    expect(decisionSwitch).not.toBeChecked();

    await user.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() =>
      expect(setupProvider).toHaveBeenCalledWith(
        'openrouter',
        expect.objectContaining({
          options: expect.objectContaining({ bind_decision: false }),
        }),
      ),
    );
  });
});
