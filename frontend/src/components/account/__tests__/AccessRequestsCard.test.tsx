import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

vi.mock('react-i18next', () => {
  const t = (key: string, opts?: unknown) => {
    if (typeof opts === 'string') return opts;
    if (opts && typeof opts === 'object' && 'defaultValue' in (opts as object)) {
      return String((opts as { defaultValue: string }).defaultValue).replace(
        /\{\{(\w+)\}\}/g,
        (_m, name: string) => String((opts as Record<string, unknown>)[name] ?? `{{${name}}}`)
      );
    }
    return key;
  };
  return { useTranslation: () => ({ t, i18n: { language: 'en' } }) };
});

vi.mock('react-toastify', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock('../../../api/axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

import api from '../../../api/axios';
import { toast } from 'react-toastify';
import { AccessRequestsCard } from '../AccessRequestsCard';
import { useAccessStore } from '../../../store/slices/accessSlice';

const PENDING = {
  id: 'g-p1',
  patient_id: 'p9',
  patient_name: 'Maria Papadopoulos',
  permission: 'write',
  source: 'patient',
  status: 'pending',
};
const ACTIVE = {
  id: 'g-a1',
  patient_id: 'p8',
  patient_name: 'Nikos Antoniou',
  permission: 'read',
  source: 'care_team',
  status: 'active',
  expires_at: '2027-01-01T00:00:00Z',
};

function mockGrants(payload: { active?: unknown[]; pending?: unknown[] }) {
  vi.mocked(api.get).mockResolvedValue({
    data: { active: payload.active ?? [], pending: payload.pending ?? [] },
  });
}

function renderCard() {
  return render(
    <MemoryRouter>
      <AccessRequestsCard />
    </MemoryRouter>
  );
}

describe('AccessRequestsCard (self-service /me/access/grants)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAccessStore.setState({
      permissions: new Set(['patient:read']),
      tenant: { id: 't1', name: 'Clinic' },
      patientGrants: [],
      pendingGrants: [],
      isHydrated: true,
      hasError: false,
    });
  });

  it('lists pending requests with a count badge and both actions', async () => {
    mockGrants({ active: [ACTIVE], pending: [PENDING] });
    renderCard();

    expect(await screen.findByText('Maria Papadopoulos')).toBeInTheDocument();
    expect(screen.getByTestId('access-pending-badge')).toHaveTextContent('1');
    expect(screen.getByRole('button', { name: 'Accept' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Decline' })).toBeInTheDocument();
    expect(screen.getByText('Records shared with you')).toBeInTheDocument();
    const link = screen.getByText('Nikos Antoniou').closest('a');
    expect(link).toHaveAttribute('href', '/patients/p8');
    expect(screen.getByText(/Expires Jan 1, 2027/)).toBeInTheDocument();
  });

  it('accepts a pending share and re-hydrates the access mirror', async () => {
    const user = userEvent.setup();
    // First fetch: the pending request. After the accept, the reload (and
    // the mirror re-hydrate) return an empty state.
    vi.mocked(api.get)
      .mockImplementationOnce(() =>
        Promise.resolve({ data: { active: [], pending: [PENDING] } })
      )
      .mockImplementation((url: string) =>
        Promise.resolve({
          data:
            url === '/me/access'
              ? { permissions: [], roles: [], tenant: {}, patient_grants: [], pending_grants: [] }
              : { active: [], pending: [] },
        })
      );
    vi.mocked(api.post).mockResolvedValue({ data: { id: 'g-p1', status: 'active' } });
    renderCard();

    await screen.findByText('Maria Papadopoulos');
    await user.click(screen.getByRole('button', { name: 'Accept' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/me/access/grants/g-p1/accept');
    });
    expect(toast.success).toHaveBeenCalledWith('Access to Maria Papadopoulos accepted.');
    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/me/access');
    });
  });

  it('declines a pending share (no reason)', async () => {
    const user = userEvent.setup();
    mockGrants({ pending: [PENDING] });
    vi.mocked(api.post).mockResolvedValue({ data: { id: 'g-p1', status: 'declined' } });
    renderCard();

    await screen.findByText('Maria Papadopoulos');
    await user.click(screen.getByRole('button', { name: 'Decline' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/me/access/grants/g-p1/decline', null);
    });
    expect(toast.info).toHaveBeenCalledWith('Request declined.');
  });

  it('shows the empty state when there are no grants or requests', async () => {
    mockGrants({});
    renderCard();

    expect(
      await screen.findByText('No care-team access requests or grants.')
    ).toBeInTheDocument();
    expect(screen.queryByTestId('access-pending-badge')).not.toBeInTheDocument();
  });

  it('surfaces a load failure inline instead of a crash', async () => {
    vi.mocked(api.get).mockRejectedValue({
      response: { status: 500, data: { detail: 'Backend down' } },
    });
    renderCard();

    expect(await screen.findByText('Backend down')).toBeInTheDocument();
  });
});
