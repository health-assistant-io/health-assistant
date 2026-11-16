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

const TENANT = {
  id: 't1',
  name: 'Clinic',
  slug: 'clinic',
  is_active: true,
  settings: { manager_scope: 'assigned', allow_patient_share: false },
};

vi.mock('../../../services/tenantService', () => ({
  getTenant: vi.fn(),
  updateMyTenant: vi.fn(),
}));
vi.mock('../../../services/patientService', () => ({ listPatients: vi.fn() }));
vi.mock('../../../services/patientAccessService', () => ({
  listPatientAccess: vi.fn(),
  getMyGrants: vi.fn().mockResolvedValue({ active: [], pending: [] }),
  acceptMyGrant: vi.fn(),
  declineMyGrant: vi.fn(),
}));

import { toast } from 'react-toastify';
import { getTenant, updateMyTenant } from '../../../services/tenantService';
import { listPatients } from '../../../services/patientService';
import { listPatientAccess } from '../../../services/patientAccessService';
import { useAccessStore } from '../../../store/slices/accessSlice';
import { AccessManagement } from '../AccessManagement';

const PATIENTS = [
  { id: 'p1', name: { family: 'Papadopoulos', given: ['Maria'] }, mrn: 'MRN-1', gender: 'female' },
  { id: 'p2', name: { family: 'Antoniou', given: ['Nikos'] }, mrn: 'MRN-2', gender: 'male' },
];

const LEDGER = [
  {
    id: 'g1',
    patient_id: 'p1',
    permission: 'write',
    source: 'care_team',
    status: 'active',
    grantee_label: 'Dr Drake',
  },
  {
    id: 'g2',
    patient_id: 'p1',
    permission: 'read',
    source: 'patient',
    status: 'revoked',
    grantee_label: 'Old Nurse',
  },
];

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

function renderPage() {
  return render(
    <MemoryRouter>
      <AccessManagement />
    </MemoryRouter>
  );
}

describe('AccessManagement (admin Access page, ADR 0054 phase 2 UI)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getTenant).mockResolvedValue(TENANT as never);
    vi.mocked(listPatients).mockResolvedValue({ items: PATIENTS, total: 2 } as never);
    vi.mocked(listPatientAccess).mockResolvedValue(LEDGER as never);
    seedAccess(['settings:read', 'settings:write', 'patient:grant', 'patient:read']);
  });

  it('shows the tenant policy with the stored manager scope and share flag', async () => {
    renderPage();

    expect(await screen.findByTestId('tenant-policy-card')).toBeInTheDocument();
    expect(screen.getByText('Assigned only')).toHaveClass('bg-blue-600');
    expect(screen.getByTestId('allow-patient-share')).not.toBeChecked();
  });

  it('saves merged tenant settings through PATCH /tenants/{id}', async () => {
    const user = userEvent.setup();
    vi.mocked(updateMyTenant).mockResolvedValue(TENANT as never);
    renderPage();

    await screen.findByTestId('tenant-policy-card');
    await user.click(screen.getByRole('button', { name: 'Whole tenant' }));
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(updateMyTenant).toHaveBeenCalledWith('t1', {
        settings: {
          ...TENANT.settings,
          manager_scope: 'tenant',
          allow_patient_share: false,
        },
      });
    });
    expect(toast.success).toHaveBeenCalledWith('Tenant policy saved.');
  });

  it('is read-only for a caller without settings:write', async () => {
    seedAccess(['settings:read', 'patient:grant']);
    renderPage();

    expect(await screen.findByText('Read-only')).toBeInTheDocument();
    expect(screen.getByTestId('allow-patient-share')).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Whole tenant' })).toBeDisabled();
  });

  it('hides the policy card without settings:read', async () => {
    seedAccess(['patient:grant']);
    renderPage();

    await screen.findByTestId('grant-inspector');
    expect(screen.queryByTestId('tenant-policy-card')).not.toBeInTheDocument();
  });

  it('inspects a patient ledger with status and grantee filters', async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByTestId('grant-inspector');
    await user.type(screen.getByPlaceholderText('Search by name or MRN…'), 'Papadopoulos');
    await user.click(screen.getByRole('button', { name: /Maria Papadopoulos/ }));

    expect(await screen.findAllByTestId('inspector-grant-row')).toHaveLength(2);

    const statusSelect = screen.getByRole('combobox', { name: 'Status' });
    await user.selectOptions(statusSelect, 'revoked');
    expect(screen.getAllByTestId('inspector-grant-row')).toHaveLength(1);
    expect(screen.getByText('Old Nurse')).toBeInTheDocument();
    expect(screen.queryByText('Dr Drake')).not.toBeInTheDocument();

    await user.selectOptions(statusSelect, 'live');
    expect(screen.getAllByTestId('inspector-grant-row')).toHaveLength(1);
    expect(screen.getByText('Dr Drake')).toBeInTheDocument();
    expect(screen.queryByText('Old Nurse')).not.toBeInTheDocument();

    await user.selectOptions(statusSelect, 'expired');
    expect(screen.getByText('No grants match these filters.')).toBeInTheDocument();

    await user.selectOptions(statusSelect, 'all');
    await user.type(screen.getByPlaceholderText('Filter by name…'), 'Drake');
    expect(screen.getAllByTestId('inspector-grant-row')).toHaveLength(1);
    expect(screen.getByText('Dr Drake')).toBeInTheDocument();
    expect(screen.queryByText('Old Nurse')).not.toBeInTheDocument();  });

  it('surfaces a ledger load error as a toast', async () => {
    const user = userEvent.setup();
    vi.mocked(listPatientAccess).mockRejectedValue({
      response: { status: 403, data: { detail: 'Access denied to this patient' } },
    });
    renderPage();

    await screen.findByTestId('grant-inspector');
    await user.type(screen.getByPlaceholderText('Search by name or MRN…'), 'Maria');
    await user.click(screen.getByRole('button', { name: /Maria Papadopoulos/ }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith('Access denied to this patient');
    });
  });
});
