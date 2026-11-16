import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('react-i18next', () => {
  const t = (key: string, fallback?: unknown) =>
    typeof fallback === 'string' ? fallback : String(key).split('.').pop();
  return {
    useTranslation: () => ({ t, i18n: { language: 'en' } }),
    initReactI18next: { type: '3rdParty', init: () => {} },
  };
});

const addMedication = vi.hoisted(() => vi.fn().mockResolvedValue({ id: 'med-1' }));
const addAllergy = vi.hoisted(() => vi.fn().mockResolvedValue({ id: 'alg-1' }));
const createEvent = vi.hoisted(() => vi.fn().mockResolvedValue({ id: 'evt-1' }));

vi.mock('../../../services/medicationService', () => ({
  addPatientMedication: addMedication,
  updatePatientMedication: vi.fn(),
}));

vi.mock('../../../services/allergyService', () => ({
  addPatientAllergy: addAllergy,
  updatePatientAllergy: vi.fn(),
  getCatalogAllergy: vi.fn().mockResolvedValue(null),
}));

vi.mock('../../../services/clinicalEventService', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    createEvent,
    updateEvent: vi.fn(),
    getEventTypes: vi.fn().mockResolvedValue([]),
    getEventCategories: vi.fn().mockResolvedValue([]),
  };
});

const searchCatalogsMock = vi.fn().mockResolvedValue({ results: [] });

vi.mock('../../../services/catalogService', () => ({
  createCatalogItem: vi.fn().mockResolvedValue({ id: 'cat-1', name: 'X' }),
  searchCatalogs: (...args: unknown[]) => searchCatalogsMock(...args),
  listCatalogItems: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  listCatalogTypes: vi.fn().mockResolvedValue({ types: [] }),
  getCatalogItem: vi.fn().mockResolvedValue({}),
  getCatalogGraph: vi.fn(async () => ({ nodes: [], edges: [] })),
  getRelationType: vi.fn(),
  getRelationTypes: vi.fn(() => []),
  loadRelationTypes: vi.fn(async () => []),
}));

vi.mock('../../../services/biomarkerService', () => ({
  __esModule: true,
  default: {
    getUnits: vi.fn().mockResolvedValue([]),
    getStates: vi.fn().mockResolvedValue([]),
    syncReferenceRanges: vi.fn().mockResolvedValue(undefined),
  },
}));

vi.mock('react-toastify', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warn: vi.fn() },
}));

vi.mock('../../../services/conceptEdges', () => ({
  getLinkSchema: vi.fn().mockResolvedValue([]),
  createLinksFor: vi.fn().mockResolvedValue({ links: [], warnings: [] }),
  selectionsToLinkInputs: vi.fn().mockReturnValue([]),
}));

vi.mock('../../ui/AIAssistButton', () => ({ AIAssistButton: () => null }));

import { MedicationModal } from '../../patients/MedicationModal';
import { AllergyModal } from '../../patients/AllergyModal';
import { ClinicalEventModal } from '../../events/ClinicalEventModal';

describe('record popup shell', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createEvent.mockResolvedValue({ id: 'evt-1' });
    searchCatalogsMock.mockResolvedValue({ results: [] });
  });

  it('MedicationModal: one close, shell header/footer, no inline define-new, writes once', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onSuccess = vi.fn();
    searchCatalogsMock.mockResolvedValue({
      results: [{ type: 'medication', id: 'm1', label: 'Ibuprofen' }],
    });
    render(
      <MedicationModal
        isOpen
        onClose={onClose}
        patientId="p1"
        onSuccess={onSuccess}
      />,
    );

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'close' })).toHaveLength(1);
    expect(screen.getByText('new_title')).toBeInTheDocument();
    expect(screen.getByText('clinical_records')).toBeInTheDocument();
    // Definitions are created from the browse dialog — the record form has
    // no inline define-new action.
    expect(screen.queryByRole('button', { name: /define_new/i })).not.toBeInTheDocument();

    const save = screen.getByRole('button', { name: 'save_medication' });
    expect(save).toBeDisabled();

    // Pick an existing catalog item (type-ahead; fireEvent — the option row
    // is portal-rendered and userEvent's pointer-events check trips on it).
    const input = screen.getByRole('combobox', { name: 'search_placeholder' });
    fireEvent.change(input, { target: { value: 'ibu' } });
    fireEvent.click(await screen.findByRole('option', { name: /Ibuprofen/ }));
    await waitFor(() => expect(save).toBeEnabled());

    await user.click(save);
    await waitFor(() => expect(addMedication).toHaveBeenCalledTimes(1));
    expect(addMedication.mock.calls[0][0]).toBe('p1');
    expect(addMedication.mock.calls[0][1].code).toEqual({
      text: 'Ibuprofen',
      catalog_id: 'm1',
    });
    expect(onSuccess).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('AllergyModal: one close, shell header/footer, closes without writing', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <AllergyModal
        isOpen
        onClose={onClose}
        patientId="p1"
        onSuccess={vi.fn()}
      />,
    );

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'close' })).toHaveLength(1);
    expect(screen.getByText('new_title')).toBeInTheDocument();
    expect(screen.getByText('Clinical record')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'save_allergy' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'cancel' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(addAllergy).not.toHaveBeenCalled();
  });

  it('ClinicalEventModal: one close, shell header/footer, closes without writing', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <ClinicalEventModal
        isOpen
        onClose={onClose}
        patientId="p1"
        onSuccess={vi.fn()}
      />,
    );

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'close' })).toHaveLength(1);
    expect(screen.getByText('new_title')).toBeInTheDocument();
    expect(screen.getByText('Clinical patient records')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'save_event' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'cancel' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(createEvent).not.toHaveBeenCalled();
  });
});
