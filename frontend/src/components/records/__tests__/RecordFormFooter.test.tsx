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

vi.mock('../../../services/catalogService', () => ({
  searchCatalogs: vi.fn().mockResolvedValue({ results: [] }),
  createCatalogItem: vi.fn().mockResolvedValue({ id: 'cat-1', name: 'X' }),
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

import { RecordFormFooter } from '../RecordFormFooter';
import { MedicationForm } from '../../patients/MedicationForm';

describe('RecordFormFooter', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('submits through the form attribute when it sits outside the form', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn((event: React.FormEvent) => event.preventDefault());
    render(
      <>
        <RecordFormFooter
          formId="record-form"
          canSubmit
          submitLabel="Save"
          cancelLabel="Cancel"
          onCancel={vi.fn()}
        />
        <form id="record-form" onSubmit={onSubmit} data-testid="record-form" />
      </>,
    );

    const save = screen.getByRole('button', { name: 'Save' });
    expect(save).toHaveAttribute('form', 'record-form');
    expect(save).toBeEnabled();

    await user.click(save);
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it('renders reject + cancel and blocks submit while invalid', async () => {
    const user = userEvent.setup();
    const onReject = vi.fn();
    const onCancel = vi.fn();
    render(
      <RecordFormFooter
        canSubmit={false}
        submitLabel="Save"
        cancelLabel="Cancel"
        onCancel={onCancel}
        rejectLabel="Reject"
        onReject={onReject}
      />,
    );

    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Reject' }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onReject).toHaveBeenCalledTimes(1);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});

describe('inline (HITL) placement', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('keeps its own form, renders the inline footer, reports state and submits', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const onStateChange = vi.fn();
    const searchCatalogs = await import('../../../services/catalogService');
    vi.mocked(searchCatalogs.searchCatalogs).mockResolvedValue({
      results: [{ type: 'medication', id: 'm1', label: 'Ibuprofen' }],
    });
    const { container } = render(
      <MedicationForm
        patientId="p1"
        onSubmit={onSubmit}
        onCancel={vi.fn()}
        onReject={vi.fn()}
        submitLabel="Confirm"
        onStateChange={onStateChange}
      />,
    );

    // The form owns its native <form> (no shell around it here).
    expect(container.querySelector('form')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument();
    await waitFor(() =>
      expect(onStateChange).toHaveBeenCalledWith({ canSubmit: false, loading: false }),
    );

    // The picker gates Save: select an existing catalog item (type-ahead;
    // fireEvent — the option row is portal-rendered).
    const input = screen.getByRole('combobox', { name: 'search_placeholder' });
    user.type(input, 'ibu');
    fireEvent.click(await screen.findByRole('option', { name: /Ibuprofen/ }));
    await waitFor(() =>
      expect(onStateChange).toHaveBeenCalledWith({ canSubmit: true, loading: false }),
    );

    // No formId: the button is a plain submit inside the form.
    const save = screen.getByRole('button', { name: 'Confirm' });
    expect(save).not.toHaveAttribute('form');
    await user.click(save);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].code).toEqual({
      text: 'Ibuprofen',
      catalog_id: 'm1',
    });
  });
});
