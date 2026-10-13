import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TaskAssignment } from './TaskAssignment';
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
  'settings.ai.fallback_label': 'Fallback',
  'settings.ai.primary_label': 'Primary',
  'settings.ai.primary_info': 'The model used whenever this task runs.',
  'settings.ai.fallback_info': 'The global default fallback is used for every task without its own assignment.',
  'settings.ai.clear_assignment': 'Clear assignment',
  'settings.ai.not_assigned_hint': 'No assignment — the {{scope}} default fallback is used.',
  'settings.ai.scope_org': 'organization',
  'settings.ai.scope_system': 'system',
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

describe('TaskAssignment', () => {
  it('renders the fallback row (fallback picker only) and the task sections', () => {
    render(<TaskAssignment scope="user" userId="u1" />);
    expect(screen.getAllByText('Global Default Fallback').length).toBeGreaterThan(0);
    expect(screen.getByText('Document Parsing (OCR)')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Fallback — Global Default Fallback' })).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: 'Global Default Fallback' })).not.toBeInTheDocument();
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

  it('shows the stored default in the fallback picker and inherit hints on unassigned tasks', () => {
    render(<TaskAssignment scope="user" userId="u1" />);
    expect(
      within(screen.getByRole('combobox', { name: 'Fallback — Global Default Fallback' }).closest('div')!).getByText('GPT-4o Clinical'),
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

  it('updates the default fallback via the secondary channel', async () => {
    const user = userEvent.setup();
    render(<TaskAssignment scope="user" userId="u1" />);
    await user.click(
      screen.getByRole('combobox', { name: 'Fallback — Global Default Fallback' }),
    );
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

  it('renders the Decision models group with the verbatim privacy notice (plan 26 §13.5)', () => {
    render(<TaskAssignment scope="user" userId="u1" />);
    expect(screen.getByText('Decision models')).toBeInTheDocument();
    expect(
      screen.getByText(
        'Decision models run in the cloud: your prompt and the candidate tools for the step are sent to the assigned provider (e.g. TypeSafe or OpenRouter). Your API key stays on this device.',
      ),
    ).toBeInTheDocument();
    for (const slot of ['Tool dispatch', 'Boolean gate', 'Choice', 'Score']) {
      expect(screen.getByText(slot)).toBeInTheDocument();
    }
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
});
