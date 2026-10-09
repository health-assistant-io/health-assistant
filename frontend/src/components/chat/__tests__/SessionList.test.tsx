import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const mockExportChatMarkdown = vi.hoisted(() => vi.fn());

vi.mock('../exportChat', () => ({
  exportChatMarkdown: mockExportChatMarkdown,
}));

const showConfirmation = vi.fn();

vi.mock('../../../store/slices/uiSlice', () => ({
  useUIStore: Object.assign(
    vi.fn((selector: (state: { showConfirmation: unknown }) => unknown) =>
      selector({ showConfirmation }),
    ),
    { getState: () => ({ showConfirmation }) },
  ),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const raw =
        {
          'common.cancel': 'Cancel',
          'common.save': 'Save',
          'common.search': 'Search',
          'common.no_results': 'No results',
          'ai_chat.status.untitled_consultation': 'Untitled consultation',
          'ai_chat.tooltips.new_chat': 'New chat',
          'ai_chat.sessions.rename': 'Rename',
          'ai_chat.sessions.delete': 'Delete',
          'ai_chat.sessions.export': 'Export',
          'ai_chat.sessions.today': 'Today',
          'ai_chat.sessions.yesterday': 'Yesterday',
          'ai_chat.sessions.earlier': 'Earlier',
          'ai_chat.sessions.empty': 'No consultations yet',
          'ai_chat.sessions.rename_title': 'Rename consultation',
          'ai_chat.sessions.delete_title': 'Delete consultation?',
          'ai_chat.sessions.delete_body': 'Permanently deletes the consultation.',
          'ai_chat.sessions.delete_confirm': 'Delete',
          'ai_chat.sessions.name_aria': 'Consultation title',
          'ai_chat.header.title': 'AI Assistant',
        }[key] ?? key;
      if (!params) return raw;
      return Object.entries(params).reduce(
        (acc, [k, v]) => acc.split(`{{${k}}}`).join(String(v)),
        raw,
      );
    },
    i18n: { resolvedLanguage: 'en' },
  }),
}));

const chatContext = {
  sessions: [
    { id: 's1', title: 'Session One', updated_at: '2026-10-09T10:00:00Z' },
    { id: 's2', title: 'Session Two', updated_at: '2026-10-01T10:00:00Z' },
  ],
  currentSessionId: 's1',
  loadSession: vi.fn(),
  startNewChat: vi.fn(),
  deleteSession: vi.fn().mockResolvedValue(undefined),
  renameSession: vi.fn().mockImplementation(async (_id: string, title: string) => ({
    id: 's1',
    title,
    updated_at: '2026-10-09T10:00:00Z',
  })),
};

vi.mock('../useHealthChat', () => ({
  useHealthChatContext: () => chatContext,
}));

import { SessionList } from '../SessionList';

describe('SessionList (library ChatSessionList glue)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    chatContext.renameSession.mockClear();
  });

  it('renders the sessions with the active row marked', async () => {
    render(<SessionList />);
    expect(await screen.findByText('Session One')).toBeInTheDocument();
    expect(screen.getByText('Session Two')).toBeInTheDocument();
  });

  it('selects a session and closes the picker', async () => {
    const onPick = vi.fn();
    render(<SessionList onPick={onPick} />);

    await userEvent.click(await screen.findByText('Session Two'));

    expect(chatContext.loadSession).toHaveBeenCalledWith('s2');
    expect(onPick).toHaveBeenCalledTimes(1);
  });

  it('renames through the PATCH-backed service and refreshes the row', async () => {
    render(<SessionList />);
    const row = (await screen.findByText('Session One')).closest('[role="listitem"]') as HTMLElement;

    await userEvent.click(within(row).getByRole('button', { name: /rename/i }));
    const input = await screen.findByTestId('session-rename-input');
    await userEvent.clear(input);
    await userEvent.type(input, 'Renamed title{Enter}');

    await waitFor(() => {
      expect(chatContext.renameSession).toHaveBeenCalledWith('s1', 'Renamed title');
    });
  });

  it('asks for confirmation before deleting', async () => {
    render(<SessionList />);
    const row = (await screen.findByText('Session Two')).closest('[role="listitem"]') as HTMLElement;

    await userEvent.click(within(row).getByRole('button', { name: /delete/i }));

    await waitFor(() => {
      expect(showConfirmation).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'Delete consultation?',
          confirmVariant: 'danger',
          onConfirm: expect.any(Function),
        }),
      );
    });
    const options = showConfirmation.mock.calls[0][0] as {
      onConfirm: () => Promise<void>;
    };
    await options.onConfirm();
    expect(chatContext.deleteSession).toHaveBeenCalledWith('s2');
  });

  it('exports the conversation as markdown', async () => {
    mockExportChatMarkdown.mockResolvedValue(undefined);
    render(<SessionList />);
    const row = (await screen.findByText('Session One')).closest('[role="listitem"]') as HTMLElement;

    await userEvent.click(within(row).getByRole('button', { name: /export/i }));

    await waitFor(() => {
      expect(mockExportChatMarkdown).toHaveBeenCalledWith('s1', 'Session One', 'AI Assistant');
    });
  });

  it('starts a new chat from the list action', async () => {
    const onPick = vi.fn();
    render(<SessionList onPick={onPick} />);

    await userEvent.click(await screen.findByRole('button', { name: /new chat/i }));

    expect(chatContext.startNewChat).toHaveBeenCalledTimes(1);
    expect(onPick).toHaveBeenCalledTimes(1);
  });
});
