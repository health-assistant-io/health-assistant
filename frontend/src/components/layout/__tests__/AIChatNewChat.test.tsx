import { describe, expect, it, vi, beforeEach } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

const mockStreamAIAssistance = vi.hoisted(() => vi.fn());
const mockGetChatSessionMessages = vi.hoisted(() => vi.fn());
const mockListChatSessions = vi.hoisted(() => vi.fn());

vi.mock('../../../services/aiAssistanceService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../services/aiAssistanceService')
  >();
  return {
    ...actual,
    streamAIAssistance: mockStreamAIAssistance,
    resumeHitlSession: vi.fn(),
    getChatSessionMessages: mockGetChatSessionMessages,
    listChatSessions: mockListChatSessions,
    transcribeAudio: vi.fn(),
  };
});
vi.mock('../../ai/hitl/HitlTaskCard', () => ({ HitlTaskCard: () => <div /> }));
vi.mock('../../ai/hitl/registry', () => ({
  TERMINAL_HITL_STATUSES: new Set(['confirmed', 'failed', 'dismissed']),
}));
vi.mock('../../ai/CitationButton', () => ({
  CitationButton: () => <span />,
}));

const uiState: any = {
  currentAiSessionId: null,
  setCurrentAiSessionId: vi.fn((id: string | null) => {
    uiState.currentAiSessionId = id;
  }),
  pendingAIMessage: null,
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
const patientState: any = { currentPatient: { id: 'p1' } };
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
vi.mock('react-toastify', () => ({ toast: { warning: vi.fn(), error: vi.fn() } }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) =>
      !params
        ? key
        : Object.entries(params).reduce(
            (acc, [k, v]) => acc.split(`{{${k}}}`).join(String(v)),
            key,
          ),
    i18n: { resolvedLanguage: 'en' },
  }),
}));

import { AIChatInterface } from '../AIChatInterface';

describe('new chat reset', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    uiState.currentAiSessionId = null;
    mockListChatSessions.mockResolvedValue([]);
    mockGetChatSessionMessages.mockResolvedValue([]);
  });

  it('new chat clears the loaded session instead of reopening it', async () => {
    mockGetChatSessionMessages.mockResolvedValue([
      { id: 'm1', role: 'assistant', content: { text: 'old session content' }, created_at: '2026-01-01T00:00:00Z' },
    ]);
    render(
      <MemoryRouter initialEntries={['/ai-assistant/s1']}>
        <AIChatInterface isFullScreen initialSessionId="s1" />
      </MemoryRouter>,
    );

    expect(await screen.findByText('old session content')).toBeInTheDocument();
    expect(mockGetChatSessionMessages).toHaveBeenCalledWith('s1');

    const newChat = await screen.findByTitle('ai_chat.tooltips.new_chat');
    await userEvent.click(newChat);
    await act(async () => {});

    expect(screen.queryByText('old session content')).not.toBeInTheDocument();
    const calls = mockGetChatSessionMessages.mock.calls.filter(
      (call) => call[0] === 's1',
    );
    expect(calls.length).toBe(1);
  });
});
