import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('react-i18next', () => {
  const t = (key: string, opts?: unknown) => {
    if (typeof opts === 'string') return opts;
    if (opts && typeof opts === 'object' && 'defaultValue' in opts) {
      return String((opts as { defaultValue: string }).defaultValue);
    }
    return key;
  };
  return { useTranslation: () => ({ t, i18n: { language: 'en' } }) };
});

vi.mock('react-toastify', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock('../../../store/slices/authSlice', () => ({
  useAuthStore: (selector: (state: { user: { id: string } }) => unknown) =>
    selector({ user: { id: 'u-self' } }),
}));

vi.mock('../../../services/elevationService', () => ({
  listElevations: vi.fn(),
  listMyElevations: vi.fn(),
  requestElevation: vi.fn(),
  approveElevation: vi.fn(),
  denyElevation: vi.fn(),
  revokeElevation: vi.fn(),
}));

import {
  approveElevation,
  listElevations,
  requestElevation,
} from '../../../services/elevationService';
import { useAccessStore } from '../../../store/slices/accessSlice';
import { ElevationQueue } from '../ElevationQueue';

const listMock = listElevations as unknown as Mock;
const approveMock = approveElevation as unknown as Mock;
const requestMock = requestElevation as unknown as Mock;

const OWN_ROW = {
  id: 'e-own',
  tenant_id: 't1',
  requested_by: 'u-self',
  approved_by: null,
  scope: 'tenant',
  status: 'pending',
  permissions: ['clinical:read'],
  reason: 'on-call coverage',
  duration_minutes: 30,
  requested_at: '2026-11-11T09:00:00Z',
  approved_at: null,
  starts_at: null,
  expires_at: null,
  revoked_at: null,
  metadata: null,
};

const OTHER_ROW = { ...OWN_ROW, id: 'e-other', requested_by: 'u-other', reason: 'incident 42' };

function seedAccess(permissions: string[]) {
  useAccessStore.setState({
    permissions: new Set(permissions),
    roles: ['ADMIN'],
    tenant: { id: 't1', name: 'Clinic' },
    patientGrants: [],
    pendingGrants: [],
    isHydrated: true,
    hasError: false,
  });
}

describe('ElevationQueue (program 12A UI)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    seedAccess(['break_glass:request', 'break_glass:approve', 'clinical:read']);
    listMock.mockResolvedValue({ items: [OWN_ROW, OTHER_ROW], total: 2 });
  });

  it('never offers a decision on the requester’s own row', async () => {
    render(<ElevationQueue scope="tenant" />);

    expect(await screen.findByTestId('elevation-row-e-own')).toBeInTheDocument();
    expect(screen.queryByTestId('elevation-approve-e-own')).not.toBeInTheDocument();
    expect(screen.queryByTestId('elevation-deny-e-own')).not.toBeInTheDocument();
    expect(screen.getByTestId('elevation-approve-e-other')).toBeInTheDocument();
    expect(screen.getByTestId('elevation-deny-e-other')).toBeInTheDocument();
  });

  it('approves another person’s pending request', async () => {
    approveMock.mockResolvedValue({ ...OTHER_ROW, status: 'active' });
    const user = userEvent.setup();
    render(<ElevationQueue scope="tenant" />);

    await user.click(await screen.findByTestId('elevation-approve-e-other'));
    await waitFor(() => expect(approveMock).toHaveBeenCalledWith('e-other'));
  });

  it('requests an elevation with the selected held permissions', async () => {
    requestMock.mockResolvedValue(OWN_ROW);
    const user = userEvent.setup();
    render(<ElevationQueue scope="tenant" />);

    await user.click(await screen.findByTestId('elevation-request-open'));
    await user.click(screen.getByTestId('elevation-permission-clinical:read'));
    await user.type(screen.getByLabelText('Reason'), 'cover the night shift');
    await user.click(screen.getByRole('button', { name: 'Request' }));

    await waitFor(() =>
      expect(requestMock).toHaveBeenCalledWith({
        scope: 'tenant',
        permissions: ['clinical:read'],
        duration_minutes: 30,
        reason: 'cover the night shift',
      }),
    );
  });

  it('hides the decision buttons without break_glass:approve but keeps requesting', async () => {
    seedAccess(['break_glass:request']);
    render(<ElevationQueue scope="tenant" />);

    expect(await screen.findByTestId('elevation-row-e-other')).toBeInTheDocument();
    expect(screen.queryByTestId('elevation-approve-e-other')).not.toBeInTheDocument();
    expect(screen.getByTestId('elevation-request-open')).toBeInTheDocument();
  });
});
