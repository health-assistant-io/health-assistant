import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TaskAssignment } from './TaskAssignment';
import { aiConfigApi } from '../../api/aiConfig';
import type { AIModel, AIProvider, AITaskAssignment } from '../../api/aiConfig';

const providers: AIProvider[] = [
  {
    id: 'prov-1',
    name: 'OpenAI Main',
    scope: 'USER',
    provider_type: 'openai',
    api_base: 'https://api.openai.com/v1',
    is_active: true,
  },
  {
    id: 'prov-ts',
    name: 'TypeSafe',
    scope: 'USER',
    provider_type: 'decision',
    api_base: 'https://api.typesafe.ai',
    is_active: true,
    preset_key: 'typesafe',
  },
];

const models: AIModel[] = [
  {
    id: 'm-chat',
    provider_id: 'prov-1',
    name: 'GPT-4o Clinical',
    model_name: 'gpt-4o',
    capabilities: ['text'],
    is_active: true,
    max_tokens: 65536,
    temperature: 0.7,
  },
  {
    id: 'm-vision',
    provider_id: 'prov-1',
    name: 'GPT-4o Vision',
    model_name: 'gpt-4o-2024-11-20',
    capabilities: ['text', 'vision'],
    is_active: true,
    max_tokens: 65536,
    temperature: 0.7,
  },
  {
    id: 'm-embed',
    provider_id: 'prov-1',
    name: 'Embed Small',
    model_name: 'text-embedding-3-small',
    capabilities: ['embeddings'],
    is_active: true,
    max_tokens: 65536,
    temperature: 0.7,
  },
  {
    id: 'm-jev',
    provider_id: 'prov-ts',
    name: 'Jev via OpenRouter (latest)',
    model_name: 'openrouter.jev-latest',
    capabilities: ['decision'],
    decision_transport: 'systemone',
    is_active: true,
    max_tokens: 65536,
    temperature: 0.7,
  },
];

const assignments: AITaskAssignment[] = [
  {
    id: 'a-default',
    task_type: 'default',
    scope: 'USER',
    provider_id: 'prov-1',
    model_id: 'm-chat',
    is_active: true,
    priority: 0,
    user_id: 'u1',
  },
];

const decisionAssignment: AITaskAssignment = {
  id: 'a-decision-td',
  task_type: 'decision_tool_dispatch',
  scope: 'USER',
  provider_id: 'prov-ts',
  model_id: 'm-jev',
  is_active: true,
  priority: 0,
  phi_allowed: false,
  user_id: 'u1',
};

const store = {
  providers,
  models,
  taskAssignments: assignments,
  createTaskAssignment: vi.fn().mockResolvedValue(assignments[0]),
  updateTaskAssignment: vi.fn().mockResolvedValue(assignments[0]),
  deleteTaskAssignment: vi.fn().mockResolvedValue(undefined),
  isLoading: false,
  error: null,
  clearError: vi.fn(),
};

vi.mock('../../store/slices/aiConfigSlice', () => ({
  useAIConfigStore: Object.assign(vi.fn(() => store), { getState: vi.fn() }),
}));

const STRINGS: Record<string, string> = {
  'settings.ai.tasks_hint': 'Assign a model to each AI task.',
  'settings.ai.section_defaults': 'Default models',
  'settings.ai.section_defaults_hint': 'The modality slots every task falls back to.',
  'settings.ai.section_decision': 'Decision models',
  'settings.ai.decision_privacy':
    'Decision models run in the cloud: your prompt and the candidate tools for the step are sent to the assigned provider (e.g. TypeSafe or OpenRouter). Your API key stays on this device.',
  'settings.ai.slot_decision_tool_dispatch': 'Tool dispatch',
  'settings.ai.slot_decision_tool_dispatch_hint': 'Picks the tool (if any) a step should use',
  'settings.ai.slot_decision_boolean_gate': 'Boolean gate',
  'settings.ai.slot_decision_boolean_gate_hint': 'Yes/no checks that guard a step',
  'settings.ai.slot_decision_choice': 'Choice',
  'settings.ai.slot_decision_choice_hint': 'Picks one option from a known set',
  'settings.ai.slot_decision_score': 'Score',
  'settings.ai.slot_decision_score_hint': 'Rates input against a rubric',
  'settings.ai.phi_override': 'Allow PHI-adjacent dispatch (patient data may be sent to this decision provider)',
  'settings.ai.section_tasks': 'Other tasks',
  'settings.ai.task_default': 'Global Default Fallback',
  'settings.ai.task_ocr': 'Document Parsing (OCR)',
  'settings.ai.task_nlp': 'Text Analysis & Extraction (NLP)',
  'settings.ai.task_medication_interaction': 'Medication Interaction Check',
  'settings.ai.task_anomaly_detection': 'Anomaly Detection',
  'settings.ai.task_fill_biomarker_form': 'Biomarker Form Auto-Fill',
  'settings.ai.task_fill_medication_form': 'Medication Form Auto-Fill',
  'settings.ai.task_magic_fill_examination': 'Magic Fill Examination',
  'settings.ai.task_define_biomarker': 'Define New Biomarker',
  'settings.ai.task_define_medication': 'Define New Medication',
  'settings.ai.task_suggest_category_icon': 'Suggest Category Icon',
  'settings.ai.task_generate_category_icon': 'Generate Custom SVG Icon',
  'settings.ai.task_chat': 'Assistant Chat',
  'settings.ai.task_transcription': 'Voice Input (Speech-to-Text)',
  'settings.ai.task_embed': 'Text Embeddings',
  // plan 29 / ADR-0036: the decision default row + Decision kinds group.
  'settings.ai.task_decision_default': 'Default decision model',
  'settings.ai.task_decision_default_hint':
    'One decision model serves all four kinds until a kind needs its own override.',
  'settings.ai.section_decision_kinds': 'Decision kinds',
  'settings.ai.decision_defaults_empty_hint':
    'Assign one decision model to power all four decision kinds — override a kind only when it needs a different model.',
  'settings.ai.decision_simplify_use_as_default': 'Simplify: use {{model}} as default',
  'settings.ai.inherited': 'Inherited',
  'settings.ai.defaults_to': 'Defaults to {{model}}',
  'settings.ai.fallback_label': 'Fallback',
  'settings.ai.primary_label': 'Primary',
  'settings.ai.primary_info': 'The model used whenever this task runs.',
  'settings.ai.fallback_info': 'The global default fallback is used for every task without its own assignment.',
  'settings.ai.clear_assignment': 'Clear assignment',
  'settings.ai.not_assigned_hint': 'No assignment — the {{scope}} default fallback is used.',
  'settings.ai.scope_org': 'organization',
  'settings.ai.scope_system': 'system',
  'settings.ai.dismiss': 'Dismiss',
  'settings.ai.suggested_default': 'Suggested: {{model}}',
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

let listPresetsSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.clearAllMocks();
  // The decision pre-suggestion fetches the preset surface on mount; tests
  // default to an empty surface and opt in where the hint is asserted.
  listPresetsSpy = vi
    .spyOn(aiConfigApi, 'listProviderPresets')
    .mockResolvedValue({ order: [], presets: {}, disabled: {} });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('TaskAssignment', () => {
  it('renders the default row as a single primary picker and the task sections (ADR-0037)', () => {
    render(<TaskAssignment scope="user" userId="u1" />);
    expect(screen.getAllByText('Global Default Fallback').length).toBeGreaterThan(0);
    expect(screen.getByText('Document Parsing (OCR)')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Global Default Fallback' })).toBeInTheDocument();
    // ADR-0037: the default row is single-picker — no fallback pickers
    // anywhere on the defaults rows.
    expect(screen.queryByRole('combobox', { name: 'Fallback — Global Default Fallback' })).not.toBeInTheDocument();
  });

  it('groups the modality slots (defaults) ahead of the derived tasks (§15 IA)', () => {
    render(<TaskAssignment scope="user" userId="u1" />);
    const defaults = screen.getByText('Default models');
    const other = screen.getByText('Other tasks');
    expect(defaults.compareDocumentPosition(other) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // vision + transcription live in the defaults group; chat is a derived task
    const defaultsGroup = defaults.closest('div')?.parentElement ?? document.body;
    expect(within(defaultsGroup).getByText('Document Parsing (OCR)')).toBeInTheDocument();
    expect(within(defaultsGroup).getByText('Voice Input (Speech-to-Text)')).toBeInTheDocument();
  });

  it('shows the stored default in its primary picker and inherit hints on unassigned tasks', () => {
    render(<TaskAssignment scope="user" userId="u1" />);
    expect(
      within(screen.getByRole('combobox', { name: 'Global Default Fallback' })).getByText('GPT-4o Clinical'),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/the organization default fallback/).length).toBeGreaterThan(0);
  });

  it('creates a new assignment scoped to the current user', async () => {
    const user = userEvent.setup();
    render(<TaskAssignment scope="user" userId="u1" />);
    await user.click(screen.getByRole('combobox', { name: 'Assistant Chat' }));
    await user.click(await screen.findByRole('option', { name: 'GPT-4o Clinical' }));
    await waitFor(() =>
      expect(store.createTaskAssignment).toHaveBeenCalledWith(
        expect.objectContaining({
          task_type: 'chat',
          scope: 'USER',
          provider_id: 'prov-1',
          model_id: 'm-chat',
          user_id: 'u1',
        }),
      ),
    );
  });

  it('filters the ocr picker to vision-capable models via requires', async () => {
    const user = userEvent.setup();
    render(<TaskAssignment scope="user" userId="u1" />);
    await user.click(screen.getByRole('combobox', { name: 'Document Parsing (OCR)' }));
    const listbox = await screen.findByRole('listbox');
    expect(within(listbox).getByRole('option', { name: 'GPT-4o Vision' })).toBeInTheDocument();
    expect(within(listbox).queryByRole('option', { name: 'GPT-4o Clinical' })).not.toBeInTheDocument();
  });

  it('updates the default through its own picker (ADR-0037: single-slot)', async () => {
    const user = userEvent.setup();
    render(<TaskAssignment scope="user" userId="u1" />);
    await user.click(screen.getByRole('combobox', { name: 'Global Default Fallback' }));
    await user.click(await screen.findByRole('option', { name: 'GPT-4o Clinical' }));
    await waitFor(() =>
      expect(store.updateTaskAssignment).toHaveBeenCalledWith(
        'a-default',
        expect.objectContaining({ model_id: 'm-chat', provider_id: 'prov-1' }),
      ),
    );
  });

  it('clears an assignment through the row-level clear button', async () => {
    const user = userEvent.setup();
    store.taskAssignments = [
      ...assignments,
      {
        id: 'a-chat',
        task_type: 'chat',
        scope: 'USER',
        provider_id: 'prov-1',
        model_id: 'm-chat',
        is_active: true,
        priority: 0,
        user_id: 'u1',
      },
    ];
    render(<TaskAssignment scope="user" userId="u1" />);
    await user.click(screen.getByRole('button', { name: 'Clear assignment — Assistant Chat' }));
    await waitFor(() => expect(store.deleteTaskAssignment).toHaveBeenCalledWith('a-chat'));
    store.taskAssignments = assignments;
  });

  it('renders the Decision kinds group with the verbatim privacy notice (plan 29 §13.5)', () => {
    render(<TaskAssignment scope="user" userId="u1" />);
    expect(screen.getByText('Decision kinds')).toBeInTheDocument();
    expect(screen.getByText('Default decision model')).toBeInTheDocument();
    expect(
      screen.getByText(
        'Decision models run in the cloud: your prompt and the candidate tools for the step are sent to the assigned provider (e.g. TypeSafe or OpenRouter). Your API key stays on this device.',
      ),
    ).toBeInTheDocument();
    for (const slot of ['Tool dispatch', 'Boolean gate', 'Choice', 'Score']) {
      expect(screen.getByText(slot)).toBeInTheDocument();
    }
    // Single-picker ruling (2026-10-14): an unset kind *is* the inherit
    // state — the trigger reads the short muted state word, with no
    // explicit inherit entry and no fallback picker anywhere on the kind
    // rows. Presentation ruling: with the default row empty, no
    // "Defaults to …" meta line either.
    expect(screen.getAllByText('Inherited')).toHaveLength(4);
    expect(screen.queryByText(/Defaults to /)).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: /^Fallback/ })).not.toBeInTheDocument();
  });

  it('shows the Inherited trigger and Defaults-to meta from the resolved decision default (presentation ruling)', () => {
    store.taskAssignments = [
      ...assignments,
      {
        id: 'a-decision-default',
        task_type: 'decision',
        scope: 'USER',
        provider_id: 'prov-ts',
        model_id: 'm-jev',
        is_active: true,
        priority: 0,
        user_id: 'u1',
      },
    ];
    render(<TaskAssignment scope="user" userId="u1" />);
    // All four kinds are empty → each trigger reads the short state word
    // and a meta line names the inherit target (the resolved Default
    // decision model).
    expect(screen.getAllByText('Inherited')).toHaveLength(4);
    expect(
      screen.getAllByText('Defaults to Jev via OpenRouter (latest)'),
    ).toHaveLength(4);
    store.taskAssignments = assignments;
  });

  it('filters the decision slots to decision-capable models via requires (D2b)', async () => {
    const user = userEvent.setup();
    render(<TaskAssignment scope="user" userId="u1" />);
    await user.click(screen.getByRole('combobox', { name: 'Tool dispatch' }));
    const listbox = await screen.findByRole('listbox');
    expect(
      within(listbox).getByRole('option', { name: 'Jev via OpenRouter (latest)' }),
    ).toBeInTheDocument();
    expect(within(listbox).queryByRole('option', { name: 'GPT-4o Clinical' })).not.toBeInTheDocument();
    // Single-picker ruling: the model list carries no explicit inherit entry.
    expect(within(listbox).queryByRole('option', { name: /Inherits/ })).not.toBeInTheDocument();
  });

  it('assigns a decision kind through its single picker (single-picker ruling)', async () => {
    const user = userEvent.setup();
    render(<TaskAssignment scope="user" userId="u1" />);
    await user.click(screen.getByRole('combobox', { name: 'Tool dispatch' }));
    await user.click(
      await screen.findByRole('option', { name: 'Jev via OpenRouter (latest)' }),
    );
    await waitFor(() =>
      expect(store.createTaskAssignment).toHaveBeenCalledWith(
        expect.objectContaining({
          task_type: 'decision_tool_dispatch',
          scope: 'USER',
          provider_id: 'prov-ts',
          model_id: 'm-jev',
        }),
      ),
    );
  });

  it('returns an assigned decision kind to inherit via the row-level clear button', async () => {
    const user = userEvent.setup();
    store.taskAssignments = [...assignments, decisionAssignment];
    render(<TaskAssignment scope="user" userId="u1" />);
    await user.click(
      screen.getByRole('button', { name: 'Clear assignment — Tool dispatch' }),
    );
    await waitFor(() =>
      expect(store.deleteTaskAssignment).toHaveBeenCalledWith('a-decision-td'),
    );
    store.taskAssignments = assignments;
  });

  it('surfaces the PHI scope guard on an assigned decision slot and flips it via the explicit opt-in (D17)', async () => {
    const user = userEvent.setup();
    store.taskAssignments = [...assignments, decisionAssignment];
    render(<TaskAssignment scope="user" userId="u1" />);
    const checkbox = screen.getByRole('checkbox', {
      name: 'Allow PHI-adjacent dispatch (patient data may be sent to this decision provider)',
    });
    expect((checkbox as HTMLInputElement).checked).toBe(false);
    await user.click(checkbox);
    await waitFor(() =>
      expect(store.updateTaskAssignment).toHaveBeenCalledWith(
        'a-decision-td',
        expect.objectContaining({ phi_allowed: true }),
      ),
    );
    store.taskAssignments = assignments;
  });

  it('hides the PHI opt-in while the decision slot is unassigned', () => {
    render(<TaskAssignment scope="user" userId="u1" />);
    expect(
      screen.queryByRole('checkbox', {
        name: /Allow PHI-adjacent dispatch/,
      }),
    ).not.toBeInTheDocument();
  });

  it('renders the embed slot in the defaults group and filters it to embeddings-capable models (§15 embeddings wave)', async () => {
    const user = userEvent.setup();
    render(<TaskAssignment scope="user" userId="u1" />);
    expect(screen.getByText('Text Embeddings')).toBeInTheDocument();
    const defaults = screen.getByText('Default models');
    const defaultsGroup = defaults.closest('div')?.parentElement ?? document.body;
    expect(within(defaultsGroup).getByText('Text Embeddings')).toBeInTheDocument();

    await user.click(screen.getByRole('combobox', { name: 'Text Embeddings' }));
    const listbox = await screen.findByRole('listbox');
    expect(within(listbox).getByRole('option', { name: 'Embed Small' })).toBeInTheDocument();
    expect(within(listbox).queryByRole('option', { name: 'GPT-4o Clinical' })).not.toBeInTheDocument();
  });

  it('creates an embed assignment through the new slot', async () => {
    const user = userEvent.setup();
    render(<TaskAssignment scope="user" userId="u1" />);
    await user.click(screen.getByRole('combobox', { name: 'Text Embeddings' }));
    await user.click(await screen.findByRole('option', { name: 'Embed Small' }));
    await waitFor(() =>
      expect(store.createTaskAssignment).toHaveBeenCalledWith(
        expect.objectContaining({
          task_type: 'embed',
          scope: 'USER',
          provider_id: 'prov-1',
          model_id: 'm-embed',
        }),
      ),
    );
  });

  it('hints the preset decision_default on EMPTY decision pickers — display-only (never auto-assigns)', async () => {
    listPresetsSpy.mockResolvedValue({
      order: ['typesafe'],
      presets: {
        typesafe: {
          key: 'typesafe',
          name: 'TypeSafe',
          provider_type: 'decision',
          wire_type: 'decision',
          base_url: 'https://api.typesafe.ai',
          fixed_base: true,
          local: false,
          decision_default: 'jev-latest',
        },
      },
      disabled: {},
    });
    render(<TaskAssignment scope="user" userId="u1" />);
    const hints = await screen.findAllByText('Suggested: jev-latest');
    expect(hints.length).toBeGreaterThan(0);
    // Display-only: a hint never mutates the store.
    expect(store.createTaskAssignment).not.toHaveBeenCalled();
    expect(store.updateTaskAssignment).not.toHaveBeenCalled();
  });

  it('shows no decision suggestion once the slot is assigned', async () => {
    listPresetsSpy.mockResolvedValue({
      order: ['typesafe'],
      presets: {
        typesafe: {
          key: 'typesafe',
          name: 'TypeSafe',
          provider_type: 'decision',
          wire_type: 'decision',
          base_url: 'https://api.typesafe.ai',
          fixed_base: true,
          local: false,
          decision_default: 'jev-latest',
        },
      },
      disabled: {},
    });
    store.taskAssignments = [...assignments, decisionAssignment];
    render(<TaskAssignment scope="user" userId="u1" />);
    await waitFor(() => expect(listPresetsSpy).toHaveBeenCalled());
    // The assigned tool-dispatch row shows the PHI guard instead; the hint
    // remains only on the three still-empty decision pickers.
    expect(screen.getAllByText('Suggested: jev-latest')).toHaveLength(3);
    expect(
      screen.queryByRole('combobox', { name: 'Tool dispatch' }),
    ).toBeInTheDocument();
    store.taskAssignments = assignments;
  });
});
