import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// Stub i18n — return the key (so we can assert presence) like the other tests.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_: string, fallback?: string) => fallback ?? 't-key' }),
}));

// Stub the observation service so no real network call fires.
const createObservationMock = vi.fn().mockResolvedValue({ id: 'obs-1' });
vi.mock('../../../services/observationService', () => ({
  createObservation: (...args: any[]) => createObservationMock(...args),
}));

// Stub the toast so it never reaches react-toastify's DOM machinery.
const toastSuccessMock = vi.fn();
vi.mock('react-toastify', () => ({
  toast: { success: (...args: any[]) => toastSuccessMock(...args), error: vi.fn() },
}));

// Stub the biomarker service — the form uses it for units + catalog search.
vi.mock('../../../services/biomarkerService', () => ({
  __esModule: true,
  default: {
    getUnits: vi.fn().mockResolvedValue([
      { id: 'u1', symbol: 'mg/dL', name: 'mg/dL', quantity_type: 'mass_concentration', conversion_multiplier: 1 },
    ]),
    getAllBiomarkers: vi.fn().mockResolvedValue([]),
  },
}));

// Stub AIAssistButton — its internals (AI provider resolution) aren't under test.
vi.mock('../../ui/AIAssistButton', () => ({
  AIAssistButton: () => <button data-testid="ai-assist-stub" />,
}));

import { LogBiomarkerReadingModal } from '../LogBiomarkerReadingModal';
import type { Biomarker } from '../../../types/biomarker';

const GLUCOSE: Biomarker = {
  id: 'bio-1',
  slug: 'glucose',
  name: 'Glucose',
  coding_system: 'loinc',
  code: '2339-0',
  aliases: [],
  preferred_unit_symbol: 'mg/dL',
  reference_range_min: 70,
  reference_range_max: 99,
  value_type: 'quantity',
};

function renderWithRouter(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

describe('LogBiomarkerReadingModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createObservationMock.mockResolvedValue({ id: 'obs-1' });
  });

  it('renders nothing when isOpen is false', () => {
    const { container } = renderWithRouter(
      <LogBiomarkerReadingModal
        isOpen={false}
        onClose={vi.fn()}
        patientId="p1"
        lockedBiomarker={GLUCOSE}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('shows the standalone-mode fields (measurement date + method) when no examination', () => {
    renderWithRouter(
      <LogBiomarkerReadingModal
        isOpen
        onClose={vi.fn()}
        patientId="p1"
        lockedBiomarker={GLUCOSE}
      />,
    );
    // Standalone mode is signaled by the "Method (optional)" text input
    // (it's hidden inside an examination where the exam's date wins).
    expect(screen.getByPlaceholderText(/Fingerstick/i)).toBeInTheDocument();
    // And the measurement-date section renders the localized "Measurement
    // Date" label — proving the standalone block is mounted.
    expect(screen.getByText('Measurement Date')).toBeInTheDocument();
  });

  it('hides the catalog search step when lockedBiomarker is supplied', () => {
    renderWithRouter(
      <LogBiomarkerReadingModal
        isOpen
        onClose={vi.fn()}
        patientId="p1"
        lockedBiomarker={GLUCOSE}
      />,
    );
    // The locked biomarker's name renders inside the lock-mode badge, but
    // the search input placeholder should NOT be in the document.
    expect(screen.queryByPlaceholderText(/Search biomarkers/i)).not.toBeInTheDocument();
    expect(screen.getByText('Glucose')).toBeInTheDocument();
  });

  it('submits a standalone observation with effective_datetime, method, and no examination_id', async () => {
    const onSuccess = vi.fn();
    const onClose = vi.fn();
    renderWithRouter(
      <LogBiomarkerReadingModal
        isOpen
        onClose={onClose}
        patientId="p1"
        lockedBiomarker={GLUCOSE}
        onSuccess={onSuccess}
      />,
    );

    // Wait for the locked-biomarker useEffect to resolve (selectedBiomarker
    // is set inside a mount effect).
    await waitFor(() => expect(screen.getByText('Glucose')).toBeInTheDocument());

    // Fill the value input (the form requires it).
    const valueInput = screen.getByPlaceholderText('0.00') as HTMLInputElement;
    fireEvent.change(valueInput, { target: { value: '95' } });

    // The submit button is the only button with bg-blue-600 inside the
    // footer (the body has bg-blue-600 interpretation toggles too, but they
    // live in the form, not the footer).
    const footer = document.querySelector('div.bg-gray-50.dark\\:bg-dark-bg\\/50.border-t') as HTMLElement;
    expect(footer).toBeTruthy();
    const submitButton = footer.querySelector('button.bg-blue-600') as HTMLButtonElement;
    expect(submitButton).toBeTruthy();

    await waitFor(() => expect(submitButton.disabled).toBe(false));

    await act(async () => {
      fireEvent.click(submitButton);
    });

    await waitFor(() => {
      expect(createObservationMock).toHaveBeenCalledTimes(1);
    });

    const payload = createObservationMock.mock.calls[0][0];
    expect(payload.patient_id).toBe('p1');
    expect(payload.biomarker_id).toBe('bio-1');
    expect(payload.examination_id).toBeUndefined();
    expect(payload.effective_datetime).toBeTruthy();
    expect(typeof payload.effective_datetime).toBe('string');
    // value_quantity is the FHIR shape for a QUANTITY biomarker.
    expect(payload.value_quantity).toMatchObject({ value: 95, unit: 'mg/dL' });

    // onSuccess + onClose fire after the save resolves.
    expect(onSuccess).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(toastSuccessMock).toHaveBeenCalledTimes(1);
  });

  it('does not call createObservation on cancel', () => {
    const onClose = vi.fn();
    renderWithRouter(
      <LogBiomarkerReadingModal
        isOpen
        onClose={onClose}
        patientId="p1"
        lockedBiomarker={GLUCOSE}
      />,
    );
    // Find the header close button — it's the X-icon button in the header
    // (which is the first icon-only button in the document).
    const closeBtn = document.querySelector('div.flex.items-center.justify-between button:last-child') as HTMLButtonElement;
    fireEvent.click(closeBtn);
    expect(createObservationMock).not.toHaveBeenCalled();
  });
});
