import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('react-toastify', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

const { showConfirmation } = vi.hoisted(() => ({
  showConfirmation: vi.fn((options: { onConfirm: () => void }) => options.onConfirm()),
}));
vi.mock('../../../store/slices/uiSlice', () => {
  const state = { showConfirmation, setPageHeaderConfig: vi.fn(), pageHeaderConfig: null };
  const useUIStore = (selector?: (value: typeof state) => unknown) =>
    selector ? selector(state) : state;
  useUIStore.getState = () => state;
  return { useUIStore };
});

vi.mock('../../../services/oauthService', () => ({
  oauthService: {
    list: vi.fn(),
    create: vi.fn(),
    rotateSecret: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
  },
}));

import { oauthService } from '../../../services/oauthService';
import OAuthClients from '../OAuthClients';

const listMock = oauthService.list as unknown as Mock;
const createMock = oauthService.create as unknown as Mock;

const CLIENT = {
  id: 'oc1',
  client_id: 'ci_existing_client',
  tenant_id: 't1',
  display_name: 'Hospital Lab Sync',
  scopes: ['system/Observation.read'],
  bound_patient_id: null,
  is_confidential: true,
  is_active: true,
  created_by_user_id: null,
};

function stubClipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  });
  return writeText;
}

describe('OAuthClients — one-time client secret', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listMock.mockResolvedValue([CLIENT]);
  });

  it('renders the registered clients', async () => {
    render(<OAuthClients />);

    expect(await screen.findByText('Hospital Lab Sync')).toBeInTheDocument();
    expect(screen.getByText('ci_existing_client')).toBeInTheDocument();
    expect(screen.getByText('system/Observation.read')).toBeInTheDocument();
  });

  it('reveals a new secret once, with the client id outside the secret block', async () => {
    createMock.mockResolvedValue({
      ...CLIENT,
      id: 'oc2',
      client_id: 'ci_new_client',
      display_name: 'Imaging Bridge',
      client_secret: 'ha_oauth_one_time_secret',
    });

    const user = userEvent.setup();
    render(<OAuthClients />);

    await user.click(await screen.findByRole('button', { name: 'New Client' }));
    await user.type(screen.getByPlaceholderText('e.g. Hospital Lab Sync'), 'Imaging Bridge');
    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith({
        display_name: 'Imaging Bridge',
        scopes: ['system/*.read'],
        bound_patient_id: undefined,
      }),
    );

    const secretBlock = await screen.findByTestId('oauth-client-secret');
    expect(secretBlock).toHaveAttribute('data-as', 'secret-once-block');
    expect(within(secretBlock).getByText('ha_oauth_one_time_secret')).toBeInTheDocument();
    expect(
      within(secretBlock).getByText(
        'Copy this secret now. For security, it cannot be retrieved again.',
      ),
    ).toBeInTheDocument();
    // The block carries exactly one secret — the client id stays app-side.
    expect(secretBlock).not.toHaveTextContent('ci_new_client');
    expect(screen.getAllByText('ci_new_client').length).toBeGreaterThan(0);

    const writeText = stubClipboard();
    await user.click(within(secretBlock).getByRole('button', { name: 'Copy' }));
    expect(writeText).toHaveBeenCalledWith('ha_oauth_one_time_secret');
    expect(await within(secretBlock).findByRole('button', { name: 'Copied' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: "I've saved it" }));
    await waitFor(() =>
      expect(screen.queryByTestId('oauth-client-secret')).not.toBeInTheDocument(),
    );
  });
});
