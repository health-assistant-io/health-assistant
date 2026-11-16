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

const addImmunization = vi.fn().mockResolvedValue({ id: 'imm-1' });
const updateImmunization = vi.fn().mockResolvedValue({ id: 'imm-1' });
vi.mock('../../../services/vaccineService', () => ({
  addPatientImmunization: (...args: unknown[]) => addImmunization(...args),
  updatePatientImmunization: (...args: unknown[]) => updateImmunization(...args),
}));

const searchCatalogsMock = vi.fn().mockResolvedValue({ results: [] });

vi.mock('../../../services/catalogService', () => ({
  searchCatalogs: (...args: unknown[]) => searchCatalogsMock(...args),
  listCatalogItems: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  listCatalogTypes: vi.fn().mockResolvedValue({ types: [] }),
  createCatalogItem: vi.fn(),
  getCatalogItem: vi.fn().mockResolvedValue({}),
  getCatalogGraph: vi.fn(async () => ({ nodes: [], edges: [] })),
  getRelationType: vi.fn(),
  getRelationTypes: vi.fn(() => []),
  loadRelationTypes: vi.fn(async () => []),
}));

vi.mock('../../../services/conceptEdges', () => ({
  getLinkSchema: vi.fn().mockResolvedValue([]),
  createLinksFor: vi.fn().mockResolvedValue({ links: [], warnings: [] }),
  selectionsToLinkInputs: vi.fn().mockReturnValue([]),
}));

import { VaccinationModal } from '../VaccinationModal';

vi.mock('react-toastify', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warn: vi.fn() },
}));

vi.mock('../../../services/biomarkerService', () => ({
  __esModule: true,
  default: {
    getUnits: vi.fn().mockResolvedValue([]),
    getStates: vi.fn().mockResolvedValue([]),
    syncReferenceRanges: vi.fn().mockResolvedValue(undefined),
  },
}));

describe('VaccinationModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    addImmunization.mockResolvedValue({ id: 'imm-1' });
    searchCatalogsMock.mockResolvedValue({ results: [] });
  });

  it('renders the shell header with exactly one close button', async () => {
    render(
      <VaccinationModal
        isOpen
        onClose={vi.fn()}
        patientId="p1"
        onSuccess={vi.fn()}
      />,
    );

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'close' })).toHaveLength(1);
    expect(screen.getByText('new_title')).toBeInTheDocument();
    expect(screen.getByText('subtitle')).toBeInTheDocument();
  });

  it('renders nothing when closed', () => {
    const { container } = render(
      <VaccinationModal
        isOpen={false}
        onClose={vi.fn()}
        patientId="p1"
        onSuccess={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('drives the shell Save from form validity and writes the record once from a picked catalog entry', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onSuccess = vi.fn();
    searchCatalogsMock.mockResolvedValue({
      results: [{ type: 'vaccine', id: 'v1', label: 'Flu' }],
    });
    render(
      <VaccinationModal
        isOpen
        onClose={onClose}
        patientId="p1"
        onSuccess={onSuccess}
      />,
    );

    await screen.findByRole('dialog');
    const save = screen.getByRole('button', { name: 'save' });
    expect(save).toBeDisabled();
    // No inline define-new: the vaccine definition is created from the
    // browse dialog, never by this modal.
    expect(screen.queryByRole('button', { name: /define_new/i })).not.toBeInTheDocument();

    // Pick an existing vaccine from the catalog (type-ahead; fireEvent —
    // the option row is portal-rendered).
    const input = screen.getByRole('combobox', { name: 'search_placeholder' });
    fireEvent.change(input, { target: { value: 'flu' } });
    fireEvent.click(await screen.findByRole('option', { name: /Flu/ }));

    await waitFor(() => expect(save).toBeEnabled());
    await user.click(save);

    await waitFor(() => expect(addImmunization).toHaveBeenCalledTimes(1));
    expect(addImmunization.mock.calls[0][0]).toBe('p1');
    const commit = addImmunization.mock.calls[0][1];
    expect(commit.vaccine_code).toEqual({ text: 'Flu', catalog_id: 'v1' });
    expect(commit.vaccine_catalog_id).toBe('v1');
    expect(onSuccess).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes without writing on Cancel', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <VaccinationModal
        isOpen
        onClose={onClose}
        patientId="p1"
        onSuccess={vi.fn()}
      />,
    );

    await screen.findByRole('dialog');
    await user.click(screen.getByRole('button', { name: 'cancel' }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(addImmunization).not.toHaveBeenCalled();
  });

  it('binds the panel footer Save to the record form as its submit button', async () => {
    render(
      <VaccinationModal
        isOpen
        onClose={vi.fn()}
        patientId="p1"
        onSuccess={vi.fn()}
      />,
    );

    await screen.findByRole('dialog');
    const save = screen.getByRole('button', { name: 'save' });
    expect(save).toHaveAttribute('type', 'submit');

    // The default-button contract: the footer button points at the form that
    // owns the fields (jsdom does not perform implicit Enter submission, so
    // the association is asserted directly; the browser walkthrough covers
    // the actual Enter key).
    const targetId = save.getAttribute('form');
    expect(targetId).toBeTruthy();
    const form = document.getElementById(targetId as string);
    expect(form).not.toBeNull();
    expect(form?.tagName).toBe('FORM');
    expect(form?.contains(screen.getByText('new_title'))).toBe(false);
  });
});
