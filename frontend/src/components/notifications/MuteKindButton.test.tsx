import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { toast } from 'react-toastify';
import api from '../../api/axios';
import { MuteKindButton } from './MuteKindButton';
import type { NotificationPreferencesHint } from '../../services/notificationService';

// Stub react-toastify — it has side effects on import.
vi.mock('react-toastify', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

function renderWithRouter(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

const mutableHint: NotificationPreferencesHint = {
  kind_id: 'source:SYSTEM',
  label: 'System notifications',
  manage_url: '/notifications/settings',
  mutable: true,
};

const immutableHint: NotificationPreferencesHint = {
  kind_id: 'source:SYSTEM',
  label: 'System notifications',
  manage_url: '/notifications/settings',
  mutable: false,
};

describe('MuteKindButton', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the mute button + manage link when mutable', () => {
    renderWithRouter(<MuteKindButton hint={mutableHint} />);
    expect(screen.getByText(/Turn off/)).toBeInTheDocument();
    expect(screen.getByText('Notification settings')).toBeInTheDocument();
  });

  it('hides the mute button but keeps the manage link when immutable', () => {
    renderWithRouter(<MuteKindButton hint={immutableHint} />);
    expect(screen.queryByText(/Turn off/)).not.toBeInTheDocument();
    expect(screen.getByText('Notification settings')).toBeInTheDocument();
  });

  it('calls the preference endpoint and shows a success toast on click', async () => {
    api.put = vi.fn().mockResolvedValue({ data: { status: 'success' } }) as any;

    renderWithRouter(<MuteKindButton hint={mutableHint} />);
    fireEvent.click(screen.getByText(/Turn off/));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith(
        '/notifications/preferences/source%3ASYSTEM',
        { enabled: false }
      );
    });
    await waitFor(() => {
      expect(toast.success).toHaveBeenCalled();
    });
  });

  it('shows an error toast when the endpoint fails', async () => {
    api.put = vi.fn().mockRejectedValue({
      response: { data: { detail: 'Not allowed' } },
    }) as any;

    renderWithRouter(<MuteKindButton hint={mutableHint} />);
    fireEvent.click(screen.getByText(/Turn off/));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith('Not allowed');
    });
  });
});
