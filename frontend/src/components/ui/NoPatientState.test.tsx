import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { NoPatientState } from './NoPatientState';

// Mock the patient store so we can control the patients list.
vi.mock('../../store/slices/patientSlice', () => ({
  usePatientStore: vi.fn(() => ({ patients: [] })),
}));

// Mock i18n — the test env doesn't load the locale bundles, so map the keys
// this component uses to their English values.
const STRINGS: Record<string, string> = {
  'common.no_patient_title': 'No Patient Selected',
  'common.no_patient_default_desc': 'Select a patient to view their dashboard.',
  'common.no_patient_select_action': 'Select a Patient',
  'common.no_patient_create_action': 'Create a New Patient',
  'common.no_patient_manage': 'Manage Patients',
  'common.no_patient_setup_action': 'Run setup wizard',
  'common.no_patient_settings': 'Settings',
};
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => STRINGS[key] ?? key }),
}));

import { usePatientStore } from '../../store/slices/patientSlice';

function renderWithRouter(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

describe('NoPatientState', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (usePatientStore as any).mockReturnValue({ patients: [] });
  });

  it('does NOT show the stale "coming soon" hint', () => {
    renderWithRouter(<NoPatientState />);
    expect(screen.queryByText(/coming soon/i)).not.toBeInTheDocument();
  });

  it('shows the "Run setup wizard" CTA', () => {
    renderWithRouter(<NoPatientState />);
    expect(screen.getByText('Run setup wizard')).toBeInTheDocument();
  });

  it('shows "Create a New Patient" when there are no patients', () => {
    (usePatientStore as any).mockReturnValue({ patients: [] });
    renderWithRouter(<NoPatientState />);
    expect(screen.getByText('Create a New Patient')).toBeInTheDocument();
  });

  it('shows "Select a Patient" when patients exist', () => {
    (usePatientStore as any).mockReturnValue({
      patients: [{ id: 'p1', name: { family: 'Doe' }, gender: 'male' }],
    });
    renderWithRouter(<NoPatientState />);
    expect(screen.getByText('Select a Patient')).toBeInTheDocument();
  });

  it('still shows the Settings action', () => {
    renderWithRouter(<NoPatientState />);
    expect(screen.getByText('Settings')).toBeInTheDocument();
  });
});
