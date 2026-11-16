import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import type { ReactElement } from 'react';

vi.mock('react-i18next', () => {
  const t = (key: string, opts?: unknown) => {
    if (typeof opts === 'string') return opts;
    if (opts && typeof opts === 'object' && 'defaultValue' in (opts as object)) {
      return String((opts as { defaultValue: string }).defaultValue).replace(
        /\{\{(\w+)\}\}/g,
        (_m, name: string) =>
          String((opts as Record<string, unknown>)[name] ?? `{{${name}}}`)
      );
    }
    return key;
  };
  return {
    useTranslation: () => ({ t, i18n: { language: 'en' } }),
  };
});

vi.mock('react-toastify', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock('../../../services/patientAccessService', () => ({
  listPatientAccess: vi.fn(),
  createPatientAccess: vi.fn(),
  updatePatientAccess: vi.fn(),
  revokePatientAccess: vi.fn(),
  grantSatisfies: vi.fn(
    (permission?: string | null, required?: string) =>
      ({ read: 0, write: 1, admin: 2 })[permission ?? '']! >=
      ({ read: 0, write: 1, admin: 2 })[required ?? 'read']!
  ),
}));
vi.mock('../../../services/doctorService', () => ({ listDoctors: vi.fn() }));
vi.mock('../../../services/userService', () => ({ listUsers: vi.fn() }));

import { toast } from 'react-toastify';
import {
  listPatientAccess,
  createPatientAccess,
  updatePatientAccess,
  revokePatientAccess,
  type AccessGrant,
} from '../../../services/patientAccessService';
import { listDoctors } from '../../../services/doctorService';
import { listUsers } from '../../../services/userService';
import { useAuthStore } from '../../../store/slices/authSlice';
import { useAccessStore } from '../../../store/slices/accessSlice';
import { useUIStore } from '../../../store/slices/uiSlice';
import { PatientAccessPanel } from '../PatientAccessPanel';
import type { Patient } from '../../../types/patient';

const PATIENT_ID = '11111111-1111-4111-8111-111111111111';

function patientFixture(ownerId?: string | null): Patient {
  return {
    id: PATIENT_ID,
    user_id: ownerId ?? null,
    name: { family: 'Papadopoulos', given: ['Maria'] },
    gender: 'female',
  };
}

const GRANTS: AccessGrant[] = [
  {
    id: 'g-active',
    patient_id: PATIENT_ID,
    grantee_user_id: 'u-doc',
    permission: 'write',
    source: 'care_team',
    status: 'active',
    granted_at: '2026-11-01T00:00:00Z',
    accepted_at: '2026-11-01T00:00:00Z',
    expires_at: '2027-01-01T00:00:00Z',
    grantee_label: 'Dr Drake',
  },
  {
    id: 'g-pending',
    patient_id: PATIENT_ID,
    grantee_doctor_id: 'd2',
    permission: 'read',
    source: 'patient',
    status: 'pending',
    granted_at: '2026-11-02T00:00:00Z',
    grantee_label: 'Dr House',
  },
  {
    id: 'g-revoked',
    patient_id: PATIENT_ID,
    grantee_user_id: 'u-old',
    permission: 'read',
    source: 'care_team',
    status: 'revoked',
    granted_at: '2026-10-01T00:00:00Z',
    grantee_label: 'Old Manager',
  },
];

function seedStaff() {
  useAuthStore.setState({
    user: { id: 'u-admin', role: 'ADMIN', email: 'admin@clinic', tenant_id: 't1', settings: {} },
  } as never);
  useAccessStore.setState({
    permissions: new Set(['patient:grant', 'patient:read', 'settings:read', 'audit:read']),
    tenant: { id: 't1', name: 'Clinic' },
    patientGrants: [],
    pendingGrants: [],
    isHydrated: true,
    hasError: false,
  });
}

function seedOwner() {
  useAuthStore.setState({
    user: { id: 'u-owner', role: 'USER', email: 'owner@clinic', tenant_id: 't1', settings: {} },
  } as never);
  useAccessStore.setState({
    permissions: new Set(['patient:grant', 'patient:read']),
    tenant: { id: 't1', name: 'Clinic' },
    patientGrants: [],
    pendingGrants: [],
    isHydrated: true,
    hasError: false,
  });
}

function renderPanel(patient: Patient) {
  return render(
    <MemoryRouter>
      <PatientAccessPanel patient={patient} />
    </MemoryRouter>
  );
}

function renderNode(ui: ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

describe('PatientAccessPanel (patient Access tab, ADR 0054 phase 2 UI)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listPatientAccess).mockResolvedValue(GRANTS);
    vi.mocked(listUsers).mockResolvedValue([
      { id: 'u-doc', email: 'drake@clinic', full_name: 'Dr Drake', role: 'MANAGER' },
    ] as never);
    vi.mocked(listDoctors).mockResolvedValue([
      { id: 'd1', name: 'Dr Linked', user_id: 'u-doc' },
      { id: 'd2', name: 'Dr House', user_id: 'u-house' },
      { id: 'd3', name: 'Dr NoAccount' },
    ] as never);
    seedStaff();
  });

  it('renders live grants and the settled history separately', async () => {
    renderPanel(patientFixture('u-owner'));

    expect(await screen.findByText('Dr Drake')).toBeInTheDocument();
    expect(screen.getByText('Dr House')).toBeInTheDocument();
    expect(screen.getByText('History')).toBeInTheDocument();
    expect(screen.getByText('Old Manager')).toBeInTheDocument();
  });

  it('shows the empty state for staff with no grants and offers the grant action', async () => {
    vi.mocked(listPatientAccess).mockResolvedValue([]);
    renderPanel(patientFixture(null));

    expect(await screen.findByText('No one has been granted access to this record yet.')).toBeInTheDocument();
    expect(screen.getAllByText('Grant access').length).toBeGreaterThan(0);
  });

  it('hides every management action from a non-owner USER grantee', async () => {
    useAuthStore.setState({
      user: { id: 'u-stranger', role: 'USER', email: 'x@clinic', tenant_id: 't1', settings: {} },
    } as never);
    renderPanel(patientFixture('u-owner'));

    expect(await screen.findByText('Dr Drake')).toBeInTheDocument();
    expect(screen.queryByText('Grant access')).not.toBeInTheDocument();
    expect(screen.queryByTitle('Edit access')).not.toBeInTheDocument();
    expect(screen.queryByTitle('Revoke')).not.toBeInTheDocument();
  });

  it('gives the owner a patient-share action instead of the staff grant action', async () => {
    seedOwner();
    renderPanel(patientFixture('u-owner'));

    expect(await screen.findByText('Dr Drake')).toBeInTheDocument();
    expect(screen.getByText('Share with my doctor')).toBeInTheDocument();
    expect(screen.queryByText('Grant access')).not.toBeInTheDocument();
    // The owner manages only the `patient` shares they initiated.
    const pendingRow = screen.getByTestId('grant-row-g-pending');
    expect(within(pendingRow as HTMLElement).getByTitle('Revoke')).toBeInTheDocument();
    const activeRow = screen.getByTestId('grant-row-g-active');
    expect(within(activeRow as HTMLElement).queryByTitle('Revoke')).not.toBeInTheDocument();
  });

  it('creates a staff care-team grant through POST', async () => {
    const user = userEvent.setup();
    vi.mocked(createPatientAccess).mockResolvedValue({ ...GRANTS[0], id: 'g-new' });
    renderPanel(patientFixture(null));

    await screen.findByText('Dr Drake');
    await user.click(screen.getAllByText('Grant access')[0]);

    const select = await screen.findByRole('combobox');
    await user.selectOptions(select, 'u-doc');
    await user.click(screen.getByRole('button', { name: 'Grant' }));

    await waitFor(() => {
      expect(createPatientAccess).toHaveBeenCalledWith(PATIENT_ID, {
        permission: 'read',
        expires_at: null,
        reason: null,
        grantee_user_id: 'u-doc',
      });
    });
    expect(listPatientAccess).toHaveBeenCalledTimes(2);
  });

  it('creates a guardian grant for an ownerless record (admin only)', async () => {
    const user = userEvent.setup();
    vi.mocked(createPatientAccess).mockResolvedValue({ ...GRANTS[0], id: 'g-guard' });
    renderPanel(patientFixture(null));

    await screen.findByText('Dr Drake');
    await user.click(screen.getAllByText('Grant access')[0]);

    await user.click(await screen.findByRole('button', { name: 'Guardian' }));
    const select = await screen.findByRole('combobox');
    await user.selectOptions(select, 'u-doc');
    // Guardian grants are active immediately — the submit reads "Grant",
    // not "Send request" (only patient shares pend on acceptance).
    await user.click(screen.getByRole('button', { name: 'Grant' }));

    await waitFor(() => {
      expect(createPatientAccess).toHaveBeenCalledWith(
        PATIENT_ID,
        expect.objectContaining({ source: 'guardian', grantee_user_id: 'u-doc' })
      );
    });
  });

  it('creates a pending patient share from the doctors directory', async () => {
    seedOwner();
    const user = userEvent.setup();
    vi.mocked(createPatientAccess).mockResolvedValue({ ...GRANTS[1], id: 'g-share' });
    renderPanel(patientFixture('u-owner'));

    await screen.findByText('Dr Drake');
    await user.click(screen.getByText('Share with my doctor'));

    const select = await screen.findByRole('combobox');
    // The unlinked doctor entry must not be offered.
    expect(within(select).getByRole('option', { name: /Dr House/ })).toBeInTheDocument();
    expect(within(select).queryByRole('option', { name: /Dr NoAccount/ })).not.toBeInTheDocument();
    await user.selectOptions(select, 'd2');
    await user.click(screen.getByRole('button', { name: 'Send request' }));

    await waitFor(() => {
      expect(createPatientAccess).toHaveBeenCalledWith(PATIENT_ID, {
        permission: 'read',
        expires_at: null,
        reason: null,
        grantee_doctor_id: 'd2',
      });
    });
  });

  it('edits a grant (permission/expiry/reason) through PATCH', async () => {
    const user = userEvent.setup();
    vi.mocked(updatePatientAccess).mockResolvedValue(GRANTS[0]);
    renderPanel(patientFixture(null));

    await screen.findByText('Dr Drake');
    await user.click(screen.getAllByTitle('Edit access')[0]);

    await user.click(await screen.findByRole('button', { name: 'Admin' }));
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(updatePatientAccess).toHaveBeenCalledWith(PATIENT_ID, 'g-active', {
        permission: 'admin',
        expires_at: '2027-01-01T00:00:00Z',
        reason: null,
      });
    });
  });

  it('revokes through the confirmation dialog', async () => {
    const user = userEvent.setup();
    vi.mocked(revokePatientAccess).mockResolvedValue({ ...GRANTS[0], status: 'revoked' });
    const showConfirmation = vi.fn();
    useUIStore.setState({ showConfirmation } as never);
    renderPanel(patientFixture(null));

    await screen.findByText('Dr Drake');
    await user.click(screen.getAllByTitle('Revoke')[0]);

    expect(showConfirmation).toHaveBeenCalledTimes(1);
    const options = showConfirmation.mock.calls[0][0] as { onConfirm: () => Promise<void> };
    await options.onConfirm();

    await waitFor(() => {
      expect(revokePatientAccess).toHaveBeenCalledWith(PATIENT_ID, 'g-active');
    });
    expect(toast.success).toHaveBeenCalledWith('Access revoked.');
  });

  it('surfaces backend error details (409/403) as toasts, never a loop', async () => {
    const user = userEvent.setup();
    vi.mocked(createPatientAccess).mockRejectedValue({
      response: { status: 409, data: { detail: 'A live grant already exists for this identity' } },
    });
    renderPanel(patientFixture(null));

    await screen.findByText('Dr Drake');
    await user.click(screen.getAllByText('Grant access')[0]);
    const select = await screen.findByRole('combobox');
    await user.selectOptions(select, 'u-doc');
    await user.click(screen.getByRole('button', { name: 'Grant' }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith('A live grant already exists for this identity');
    });
  });

  it('shows the ledger load error inline (403 on a foreign record)', async () => {
    vi.mocked(listPatientAccess).mockRejectedValue({
      response: { status: 403, data: { detail: 'Access denied to this patient' } },
    });
    renderNode(<PatientAccessPanel patient={patientFixture(null)} />);

    expect(await screen.findByText('Access denied to this patient')).toBeInTheDocument();
    expect(screen.queryByText('Grant access')).not.toBeInTheDocument();
  });

  it('links SYSTEM_ADMIN audit readers to the tenant audit surface', async () => {
    useAuthStore.setState({
      user: { id: 'u-sys', role: 'SYSTEM_ADMIN', email: 'sys@local', tenant_id: 't1', settings: {} },
    } as never);
    renderPanel(patientFixture(null));

    expect(await screen.findByText('Dr Drake')).toBeInTheDocument();
    expect(screen.getByText('Audit log').closest('a')).toHaveAttribute(
      'href',
      '/admin/system/tenants/t1'
    );
  });
});
