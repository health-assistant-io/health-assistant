import { describe, expect, it, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

const mockStreamAIAssistance = vi.hoisted(() => vi.fn());
const mockResumeHitlSession = vi.hoisted(() => vi.fn());
const mockGetChatSessionMessages = vi.hoisted(() => vi.fn());
const mockListChatSessions = vi.hoisted(() => vi.fn());

vi.mock('../../../services/aiAssistanceService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../services/aiAssistanceService')
  >();
  return {
    ...actual,
    streamAIAssistance: mockStreamAIAssistance,
    resumeHitlSession: mockResumeHitlSession,
    getChatSessionMessages: mockGetChatSessionMessages,
    listChatSessions: mockListChatSessions,
    transcribeAudio: vi.fn(),
  };
});

vi.mock('../../ai/hitl/HitlTaskCard', () => ({
  HitlTaskCard: ({ task, onResolved }: {
    task: { proposal_id: string; task_type: string; status: string; title?: string };
    onResolved: (updated: unknown) => void;
  }) => (
    <div data-testid={`hitl-card-${task.proposal_id}`}>
      <span>
        {task.title ?? task.task_type} — {task.status}
      </span>
      <button
        type="button"
        onClick={() => onResolved({ ...task, status: 'confirmed' })}
      >
        resolve
      </button>
    </div>
  ),
}));

vi.mock('../../ai/hitl/registry', () => ({
  TERMINAL_HITL_STATUSES: new Set(['confirmed', 'failed', 'dismissed']),
}));

vi.mock('../../ai/CitationButton', () => ({
  CitationButton: ({ reference }: { reference: string }) => (
    <button type="button" data-testid={`citation-${reference.toLowerCase()}`}>
      cite:{reference}
    </button>
  ),
}));

const uiState = {
  currentAiSessionId: null as string | null,
  setCurrentAiSessionId: vi.fn(),
  pendingAIMessage: null as string | null,
  setPendingAIMessage: vi.fn(),
  lastNonAiPath: '/',
  setAIDrawerOpen: vi.fn(),
  currentExaminationId: null,
  currentBiomarkerId: null,
  currentMedicationId: null,
  currentAllergyId: null,
  showConfirmation: vi.fn(),
};

vi.mock('../../../store/slices/uiSlice', () => ({
  useUIStore: Object.assign(
    vi.fn((selector: (state: typeof uiState) => unknown) => selector(uiState)),
    { getState: () => uiState },
  ),
}));

const patientState = { currentPatient: { id: 'patient-1' } };

vi.mock('../../../store/slices/patientSlice', () => ({
  usePatientStore: Object.assign(
    vi.fn((selector: (state: typeof patientState) => unknown) =>
      selector(patientState),
    ),
    { getState: () => patientState },
  ),
}));

vi.mock('../../../store/slices/aiConfigSlice', () => ({
  useAIConfigStore: Object.assign(
    vi.fn(() => ({ providers: [], models: [] })),
    { getState: () => ({ providers: [], models: [] }) },
  ),
}));

vi.mock('react-toastify', () => ({
  toast: { warning: vi.fn(), error: vi.fn() },
}));

const STRINGS: Record<string, string> = {
  'ai_chat.welcome_message': 'Welcome — how can I help?',
  'ai_chat.header.title': 'AI Assistant',
  'ai_chat.header.subtitle': 'Your clinical copilot',
  'ai_chat.tabs.chat': 'Chat',
  'ai_chat.tabs.insights': 'Insights',
  'ai_chat.tabs.actions': 'Actions',
  'ai_chat.input.placeholder': 'Ask anything',
  'ai_chat.input.disclaimer': 'AI guidance is not a diagnosis.',
  'ai_chat.tooltips.send': 'Send',
  'ai_chat.tooltips.stop': 'Stop',
  'ai_chat.tooltips.copy': 'Copy',
  'ai_chat.tooltips.close': 'Close chat',
  'ai_chat.tooltips.new_chat': 'New chat',
  'ai_chat.tooltips.history': 'History',
  'ai_chat.tooltips.ledger': 'Ledger',
  'ai_chat.tooltips.agent_capabilities': 'Capabilities',
  'ai_chat.tooltips.full_screen': 'Expand',
  'ai_chat.tooltips.minimize': 'Minimize',
  'ai_chat.tooltips.resize': 'Resize',
  'ai_chat.errors.connection': 'Could not reach the AI service.',
  'ai_chat.errors.generic': 'Something went wrong.',
  'ai_chat.flow.think': 'Thinking',
  'ai_chat.flow.tools': 'Using tools',
  'ai_chat.flow.await': 'Waiting for you',
  'ai_chat.flow.finalize': 'Wrapping up',
  'ai_chat.flow.elapsed': 'elapsed',
  'ai_chat.flow.tools_running': 'Running',
  'ai_chat.flow.tools_done': 'Done',
  'ai_chat.flow.tools_failed': 'Failed',
  'ai_chat.inspector.args': 'Arguments',
  'ai_chat.inspector.result': 'Result',
  'ai_chat.transcript.aria': 'Conversation',
  'ai_chat.transcript.scroll_down': 'Scroll to latest',
  'ai_chat.voice.record': 'Dictate',
  'ai_chat.voice.recording': 'Recording',
  'ai_chat.voice.transcribing': 'Transcribing',
  'ai_chat.voice.stop_label': 'Stop',
  'ai_chat.voice.cancel': 'Cancel',
  'ai_chat.voice.dismiss': 'Dismiss',
  'ai_chat.voice.unsupported': 'Unsupported',
  'ai_chat.voice.permission_denied': 'Denied',
  'ai_chat.voice.unassigned': 'Unassigned',
  'ai_chat.voice.failed': 'Failed',
  'ai_chat.attachments.drop': 'Drop images',
  'ai_chat.hitl.continue_partial': 'Continue',
  'ai_chat.insights.title': 'Insights',
  'ai_chat.insights.select_patient': 'Select a patient',
  'ai_chat.actions.title': 'Actions',
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

import { AIChatInterface } from '../AIChatInterface';
import type { TaskInfo } from '../../../types/ai';

type StreamCallbacks = {
  onMessage: (msg: unknown) => void;
  onComplete: () => void;
  onError: (error: unknown) => void;
};

function captureStream(): StreamCallbacks {
  const captured = {} as StreamCallbacks;
  mockStreamAIAssistance.mockImplementationOnce(
    async (
      _request: unknown,
      onMessage: StreamCallbacks['onMessage'],
      onComplete: StreamCallbacks['onComplete'],
      onError: StreamCallbacks['onError'],
    ) => {
      captured.onMessage = onMessage;
      captured.onComplete = onComplete;
      captured.onError = onError;
    },
  );
  return captured;
}

function captureResume(): StreamCallbacks {
  const captured = {} as StreamCallbacks;
  mockResumeHitlSession.mockImplementationOnce(
    async (
      _id: string,
      _params: unknown,
      onMessage: StreamCallbacks['onMessage'],
      onComplete: StreamCallbacks['onComplete'],
      onError: StreamCallbacks['onError'],
    ) => {
      captured.onMessage = onMessage;
      captured.onComplete = onComplete;
      captured.onError = onError;
    },
  );
  return captured;
}

const task = (proposalId: string, status: TaskInfo['status']): TaskInfo => ({
  schema_version: 1,
  proposal_id: proposalId,
  task_type: 'create_medication',
  title: `Proposal ${proposalId}`,
  status,
  proposed_payload: {},
});

function sessionMessageRow(overrides: Record<string, unknown> = {}) {
  return {
    id: 'm1',
    role: 'assistant',
    content: { text: 'Loaded answer' },
    tool_calls: [
      { name: 'get_recent_observations', args: '{}', result: '[1]' },
    ],
    citations: ['get_recent_observations'],
    tasks: [task('p1', 'proposed')],
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function renderInterface(props: Record<string, unknown> = {}) {
  return render(
    <MemoryRouter>
      <AIChatInterface isFullScreen={false} {...props} />
    </MemoryRouter>,
  );
}

describe('AIChatInterface on the library core', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    uiState.currentAiSessionId = null;
    uiState.pendingAIMessage = null;
    mockListChatSessions.mockResolvedValue([
      { id: 's1', title: 'Session One', updated_at: '2026-01-02T00:00:00Z' },
    ]);
    mockGetChatSessionMessages.mockResolvedValue([]);
  });

  it('renders the welcome message as the empty state', async () => {
    renderInterface();
    expect(await screen.findByText('Welcome — how can I help?')).toBeInTheDocument();
  });

  it('loads a session from the store pointer and renders rows with tool cards and HITL slots', async () => {
    uiState.currentAiSessionId = 's1';
    mockGetChatSessionMessages.mockResolvedValue([
      { id: 'm0', role: 'user', content: { text: 'question' }, created_at: '2026-01-01T00:00:00Z' },
      sessionMessageRow(),
    ]);

    renderInterface();

    expect(await screen.findByText('Loaded answer')).toBeInTheDocument();
    expect(screen.getByTestId('hitl-card-p1')).toBeInTheDocument();
    expect(screen.getByText('get_recent_observations')).toBeInTheDocument();
    expect(mockGetChatSessionMessages).toHaveBeenCalledWith('s1');
  });

  it('streams a turn through the live tail and commits it locally on completion', async () => {
    const user = userEvent.setup();
    const captured = captureStream();
    renderInterface();

    const input = await screen.findByLabelText('Ask anything');
    await user.type(input, 'hello');
    await user.keyboard('{Enter}');

    expect(await screen.findByText('hello')).toBeInTheDocument();

    await act(async () => { captured.onMessage({ flowEvent: { event: 'node_started', node: 'agent_step' } }); });
    expect(await screen.findByText('Thinking')).toBeInTheDocument();

    await act(async () => { captured.onMessage({ content: 'streamed answer' }); });
    expect(await screen.findByText('streamed answer')).toBeInTheDocument();

    await act(async () => { captured.onMessage({
      toolCall: { name: 'get_recent_observations', status: 'finished', result: '[1]' },
    }); });
    await act(async () => { captured.onComplete(); });

    // The streamed row stays committed locally (no post-turn refetch —
    // history reload is the only refetch path, preserving HITL auto-resume).
    expect(await screen.findByText('streamed answer')).toBeInTheDocument();
    expect(mockGetChatSessionMessages).not.toHaveBeenCalled();
  });

  it('renders the error state with the localized message and keeps it after the refetch', async () => {
    const user = userEvent.setup();
    mockGetChatSessionMessages.mockResolvedValue([]);
    const captured = captureStream();
    renderInterface();

    const input = await screen.findByLabelText('Ask anything');
    await user.type(input, 'hello');
    await user.keyboard('{Enter}');

    await act(async () => { captured.onMessage({ error: '', errorType: 'connection' }); });
    await act(async () => { captured.onComplete(); });

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('connection');
    expect(alert).toHaveTextContent('Could not reach the AI service.');
  });

  it('maps the resumable HITL pause onto the interrupted live state, then commits at stream close', async () => {
    const user = userEvent.setup();
    const captured = captureStream();
    renderInterface();

    const input = await screen.findByLabelText('Ask anything');
    await user.type(input, 'hello');
    await user.keyboard('{Enter}');

    await act(async () => { captured.onMessage({ content: 'partial answer' }); });
    await act(async () => { captured.onMessage({ task: task('live-1', 'proposed') }); });
    await act(async () => { captured.onMessage({ flowEvent: { event: 'interrupt' } }); });

    expect(await screen.findByText('Stopped')).toBeInTheDocument();
    expect(screen.getByText('partial answer')).toBeInTheDocument();
    expect(screen.getByTestId('hitl-card-live-1')).toBeInTheDocument();

    await act(async () => { captured.onComplete(); });

    // Stream close terminalizes: the row commits locally with its tasks.
    await waitFor(() => {
      expect(screen.getByTestId('hitl-card-live-1')).toBeInTheDocument();
      expect(screen.queryByText('Stopped')).not.toBeInTheDocument();
    });
    expect(screen.getByText('partial answer')).toBeInTheDocument();
  });

  it('renders live HITL cards in the live tail and resolves them through the payload path', async () => {
    const user = userEvent.setup();
    mockGetChatSessionMessages.mockResolvedValue([]);
    const captured = captureStream();
    renderInterface();

    const input = await screen.findByLabelText('Ask anything');
    await user.type(input, 'hello');
    await user.keyboard('{Enter}');

    await act(async () => { captured.onMessage({ task: task('live-1', 'proposed') }); });
    expect(await screen.findByTestId('hitl-card-live-1')).toBeInTheDocument();
  });

  it('fires the HITL continuation once when the last task resolves on a streamed row', async () => {
    const user = userEvent.setup();
    const resumeCallbacks = captureResume();
    const captured = captureStream();
    renderInterface();

    const input = await screen.findByLabelText('Ask anything');
    await user.type(input, 'question');
    await user.keyboard('{Enter}');

    await act(async () => { captured.onMessage({ sessionId: 's1' }); });
    await act(async () => { captured.onMessage({ content: 'here is a proposal' }); });
    await act(async () => { captured.onMessage({ task: task('p1', 'proposed') }); });
    await act(async () => { captured.onComplete(); });

    const card = await screen.findByTestId('hitl-card-p1');
    await user.click(card.querySelector('button')!);

    await waitFor(() => {
      expect(mockResumeHitlSession).toHaveBeenCalledTimes(1);
      expect(mockResumeHitlSession).toHaveBeenCalledWith(
        's1',
        {},
        expect.any(Function),
        expect.any(Function),
        expect.any(Function),
      );
    });
    expect(await screen.findByText('Proposal p1 — confirmed')).toBeInTheDocument();

    await act(async () => { resumeCallbacks.onMessage({ content: 'continuation' }); });
    await act(async () => { resumeCallbacks.onComplete(); });
    expect(await screen.findByText('continuation')).toBeInTheDocument();
  });

  it('does not auto-resume tasks loaded from history', async () => {
    uiState.currentAiSessionId = 's1';
    mockGetChatSessionMessages.mockResolvedValue([
      { id: 'm0', role: 'user', content: { text: 'question' }, created_at: '2026-01-01T00:00:00Z' },
      sessionMessageRow({ tasks: [task('p1', 'proposed')] }),
    ]);
    renderInterface();
    const card = await screen.findByTestId('hitl-card-p1');

    await userEvent.click(card.querySelector('button')!);

    expect(await screen.findByText('Proposal p1 — confirmed')).toBeInTheDocument();
    await waitFor(() => {
      expect(mockResumeHitlSession).not.toHaveBeenCalled();
    });
  });

  it('shows the Continue button for partial HITL resolutions on a streamed row and resumes on click', async () => {
    const user = userEvent.setup();
    captureResume();
    const captured = captureStream();
    renderInterface();

    const input = await screen.findByLabelText('Ask anything');
    await user.type(input, 'question');
    await user.keyboard('{Enter}');

    await act(async () => { captured.onMessage({ sessionId: 's1' }); });
    await act(async () => { captured.onMessage({ content: 'two proposals' }); });
    await act(async () => { captured.onMessage({ task: task('t1', 'proposed') }); });
    await act(async () => { captured.onMessage({ task: task('t2', 'proposed') }); });
    await act(async () => { captured.onComplete(); });

    // Resolve ONE of the two cards: partial — the Continue button appears.
    const first = await screen.findByTestId('hitl-card-t1');
    await user.click(first.querySelector('button')!);
    expect(await screen.findByText('Proposal t1 — confirmed')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: /Continue/i })).toBeInTheDocument();
    expect(mockResumeHitlSession).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /Continue/i }));
    await waitFor(() => {
      expect(mockResumeHitlSession).toHaveBeenCalledTimes(1);
    });
  });

  it('auto-resumes only after the LAST of several HITL cards resolves on a streamed row', async () => {
    const user = userEvent.setup();
    captureResume();
    const captured = captureStream();
    renderInterface();

    const input = await screen.findByLabelText('Ask anything');
    await user.type(input, 'question');
    await user.keyboard('{Enter}');

    await act(async () => { captured.onMessage({ sessionId: 's1' }); });
    await act(async () => { captured.onMessage({ task: task('t1', 'proposed') }); });
    await act(async () => { captured.onMessage({ task: task('t2', 'proposed') }); });
    await act(async () => { captured.onComplete(); });

    const first = await screen.findByTestId('hitl-card-t1');
    await user.click(first.querySelector('button')!);
    await waitFor(() => {
      expect(mockResumeHitlSession).not.toHaveBeenCalled();
    });

    const second = screen.getByTestId('hitl-card-t2');
    await user.click(second.querySelector('button')!);

    await waitFor(() => {
      expect(mockResumeHitlSession).toHaveBeenCalledTimes(1);
    });
  });
});
