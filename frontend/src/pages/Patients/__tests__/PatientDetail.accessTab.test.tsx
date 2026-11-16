import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

vi.mock('react-i18next', () => {
  const t = (key: string, opts?: unknown) =>
    typeof opts === 'string' ? opts : key;
  return { useTranslation: () => ({ t, i18n: { language: 'en' } }) };
});

vi.mock('react-toastify', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock('../../../services/patientService', () => ({
  getPatient: vi.fn(),
  deletePatient: vi.fn(),
}));
vi.mock('../../../services/patientAccessService', () => ({
  listPatientAccess: vi.fn().mockResolvedValue([]),
  createPatientAccess: vi.fn(),
  updatePatientAccess: vi.fn(),
  revokePatientAccess: vi.fn(),
  grantSatisfies: vi.fn(() => false),
}));
vi.mock('../../../services/doctorService', () => ({ listDoctors: vi.fn().mockResolvedValue([]) }));
vi.mock('../../../services/userService', () => ({ listUsers: vi.fn().mockResolvedValue([]) }));

// The overview tab's summary cards each fetch their own services — stub them.
vi.mock('../../../components/patients/AllergySummary', () => ({
  AllergySummary: () => <div data-testid="allergy-summary" />,
}));
vi.mock('../../../components/patients/MedicationSummary', () => ({
  MedicationSummary: () => <div data-testid="medication-summary" />,
}));
vi.mock('../../../components/patients/BiomarkerSummary', () => ({
  default: () => <div data-testid="biomarker-summary" />,
}));
vi.mock('../../../components/patients/ExaminationSummary', () => ({
  default: () => <div data-testid="examination-summary" />,
}));
vi.mock('../../../components/patients/ClinicalEventSummary', () => ({
  default: () => <div data-testid="event-summary" />,
}));
vi.mock('../../../components/patients/ScheduleSummary', () => ({
  default: () => <div data-testid="schedule-summary" />,
}));
vi.mock('../../../components/setup/SetupChecklistCard', () => ({
  SetupChecklistCard: () => <div data-testid="setup-checklist" />,
}));

import { getPatient } from '../../../services/patientService';
import { listPatientAccess } from '../../../services/patientAccessService';
import { useAuthStore } from '../../../store/slices/authSlice';
import { useAccessStore } from '../../../store/slices/accessSlice';
import PatientDetail from '../PatientDetail';

const PATIENT = {
  id: 'p1',
  user_id: 'u-owner',
  name: { family: 'Papadopoulos', given: ['Maria'] },
  gender: 'female',
};

describe('PatientDetail Access tab routing (plan D phase 2 UI)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getPatient).mockResolvedValue(PATIENT as never);
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
  });

  function renderAt(path: string) {
    return render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/patients/:patientId" element={<PatientDetail />} />
          <Route path="/patients/:patientId/:activeTab" element={<PatientDetail />} />
        </Routes>
      </MemoryRouter>
    );
  }

  it('renders the overview tab by default with the Access tab available', async () => {
    renderAt('/patients/p1');

    expect(await screen.findByTestId('allergy-summary')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Access' })).toBeInTheDocument();
    expect(screen.queryByTestId('grant-inspector')).not.toBeInTheDocument();
  });

  it('mounts the Access panel on /patients/{id}/access and loads the ledger', async () => {
    renderAt('/patients/p1/access');

    expect(
      (await screen.findAllByRole('button', { name: 'Share with my doctor' })).length
    ).toBeGreaterThan(0);
    await waitFor(() => {
      expect(listPatientAccess).toHaveBeenCalledWith('p1');
    });
    expect(screen.queryByTestId('allergy-summary')).not.toBeInTheDocument();
  });

  it('switches between the tabs', async () => {
    const user = userEvent.setup();
    renderAt('/patients/p1');

    await screen.findByTestId('allergy-summary');
    await user.click(screen.getByRole('button', { name: 'Access' }));

    await waitFor(() => {
      expect(listPatientAccess).toHaveBeenCalledWith('p1');
    });
    expect(screen.queryByTestId('allergy-summary')).not.toBeInTheDocument();
  });
});
