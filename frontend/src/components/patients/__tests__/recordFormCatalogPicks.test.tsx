/**
 * Record-form catalog-pick tests (definitions are created from the browse
 * dialog — the record forms only PICK).
 *
 * Covers:
 *   1. No inline define-new action in the record forms; Save is gated on an
 *      existing catalog selection.
 *   2. Magic Fill matched name → the picker selects the catalog hit and the
 *      payload's `code` comes from the selection only.
 *   3. Magic Fill unmatched name → toast, picker stays empty (no orphan state).
 *   4. AllergyForm keeps the patient-level category sync from the picked
 *      catalog entry.
 *   5. The `catalogSelection` one-shot contract: applies when the id changes
 *      and the picker doesn't already hold it; never re-applies after the
 *      user clears the chip; no-op on remount with the same value.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, dftOrOpts?: unknown, opts?: Record<string, unknown>) => {
      let s =
        typeof dftOrOpts === 'string'
          ? dftOrOpts
          : ((dftOrOpts as { defaultValue?: string })?.defaultValue ?? k.split('.').pop() ?? k);
      const vars = { ...(typeof dftOrOpts === 'object' ? (dftOrOpts ?? {}) : {}), ...(opts ?? {}) };
      for (const [key, val] of Object.entries(vars)) {
        if (key === 'defaultValue') continue;
        s = s.replace(new RegExp(`{{\\s*${key}\\s*}}`, 'g'), String(val));
      }
      return s;
    },
    i18n: { language: 'en' },
  }),
}));

vi.mock('react-toastify', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warn: vi.fn() },
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
  getRelationTypes: vi.fn((): string[] => []),
  loadRelationTypes: vi.fn(async (): Promise<string[]> => []),
}));

vi.mock('../../../services/biomarkerService', () => ({
  __esModule: true,
  default: {
    getUnits: vi.fn().mockResolvedValue([]),
    getStates: vi.fn().mockResolvedValue([]),
    getAllBiomarkers: vi.fn().mockResolvedValue([]),
    getBiomarkerById: vi.fn().mockResolvedValue(null),
    syncReferenceRanges: vi.fn().mockResolvedValue(undefined),
  },
}));

const getCatalogAllergy = vi.fn().mockResolvedValue(null);
vi.mock('../../../services/allergyService', () => ({
  getCatalogAllergy: (...args: unknown[]) => getCatalogAllergy(...args),
  addPatientAllergy: vi.fn(),
  updatePatientAllergy: vi.fn(),
}));

vi.mock('../../../services/medicationService', () => ({
  addPatientMedication: vi.fn(),
  updatePatientMedication: vi.fn(),
}));

vi.mock('../../../services/conceptEdges', () => ({
  getLinkSchema: vi.fn().mockResolvedValue([]),
  createLinksFor: vi.fn().mockResolvedValue([]),
  selectionsToLinkInputs: vi.fn(() => []),
}));

const assistHandlers: Record<string, (data: Record<string, unknown>) => void> = {};
vi.mock('../../ui/AIAssistButton', () => ({
  AIAssistButton: ({
    taskType,
    onSuggestedData,
  }: {
    taskType: string;
    onSuggestedData: (data: Record<string, unknown>) => void;
  }) => {
    assistHandlers[taskType] = onSuggestedData;
    return (
      <button type="button" onClick={() => onSuggestedData(latestSuggestion)}>
        assist-{taskType}
      </button>
    );
  },
}));

vi.mock('../../ui/RichTextEditor', () => ({ RichTextEditor: () => <div /> }));

let latestSuggestion: Record<string, unknown> = {};

import { MedicationForm } from '../MedicationForm';
import { AllergyForm } from '../AllergyForm';
import { toast } from 'react-toastify';
import type { MedicationFormPayload } from '../MedicationForm';
import type { AllergyFormPayload } from '../AllergyForm';

beforeEach(() => {
  vi.clearAllMocks();
  searchCatalogsMock.mockResolvedValue({ results: [] });
  getCatalogAllergy.mockResolvedValue(null);
  latestSuggestion = {};
});

describe('MedicationForm (record) — pick-only catalog flow', () => {
  it('has no define-new action and Save is gated on a selection', () => {
    render(
      <MedicationForm patientId="p1" onSubmit={vi.fn().mockResolvedValue(undefined)} />,
    );
    expect(screen.queryByText('define_new')).not.toBeInTheDocument();
    expect(screen.queryByText('add_custom')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'save_medication' })).toBeDisabled();
  });

  it('Magic Fill matched name selects the catalog hit and the payload code comes from it', async () => {
    searchCatalogsMock.mockResolvedValue({
      results: [{ type: 'medication', id: 'm1', label: 'Metformin' }],
    });
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <MedicationForm patientId="p1" onSubmit={onSubmit} />,
    );

    latestSuggestion = { medication_name: 'Metformin', dosage: '500mg' };
    await userEvent.click(screen.getByRole('button', { name: 'assist-fill_medication_form' }));

    await waitFor(() => expect(screen.getByText('Metformin')).toBeInTheDocument());
    const save = screen.getByRole('button', { name: 'save_medication' });
    await waitFor(() => expect(save).toBeEnabled());
    await userEvent.click(save);

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const payload = onSubmit.mock.calls[0][0] as MedicationFormPayload;
    expect(payload.code).toEqual({ text: 'Metformin', catalog_id: 'm1' });
    expect(payload).not.toHaveProperty('is_new_catalog_entry');
  });

  it('Magic Fill unmatched name toasts and the picker stays empty', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <MedicationForm patientId="p1" onSubmit={onSubmit} />,
    );

    latestSuggestion = { medication_name: 'Nonexistol' };
    await userEvent.click(screen.getByRole('button', { name: 'assist-fill_medication_form' }));

    await waitFor(() => expect(toast.info).toHaveBeenCalled());
    expect(String(vi.mocked(toast.info).mock.calls[0][0])).toContain('Nonexistol');
    expect(screen.getByRole('button', { name: 'save_medication' })).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('catalogSelection one-shot: applies once on change, no-op on remount, never re-applies after clear', async () => {
    const Harness = ({ catalogSelection }: { catalogSelection?: { catalog_id: string; text: string } }) => (
      <MedicationForm
        patientId="p1"
        onSubmit={vi.fn().mockResolvedValue(undefined)}
        catalogSelection={catalogSelection}
      />
    );

    // 1. Mount WITH the value already set → no-op (remount with the same
    //    value never re-applies).
    const first = render(<Harness catalogSelection={{ catalog_id: 'c1', text: 'Picked Med' }} />);
    expect(screen.queryByText('Picked Med')).not.toBeInTheDocument();

    // 2. The id CHANGES → the picker is set exactly once.
    first.rerender(<Harness catalogSelection={{ catalog_id: 'c2', text: 'New Med' }} />);
    await waitFor(() => expect(screen.getByText('New Med')).toBeInTheDocument());
    expect(screen.queryByText('Picked Med')).not.toBeInTheDocument();

    // 3. The user clears the chip; the unchanged prop does NOT re-apply.
    await userEvent.click(screen.getAllByTitle('Remove')[0]);
    await waitFor(() => expect(screen.queryByText('New Med')).not.toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'save_medication' })).toBeDisabled(),
    );
  });
});

describe('AllergyForm (record) — pick-only catalog flow', () => {
  it('has no define-new action and Save is gated on a selection', () => {
    render(<AllergyForm patientId="p1" onSubmit={vi.fn().mockResolvedValue(undefined)} />);
    expect(screen.queryByText('define_new')).not.toBeInTheDocument();
    expect(screen.queryByText('add_custom')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'save_allergy' })).toBeDisabled();
  });

  it('Magic Fill unmatched name toasts and leaves the picker empty', async () => {
    render(<AllergyForm patientId="p1" onSubmit={vi.fn().mockResolvedValue(undefined)} />);

    latestSuggestion = { name: 'Unobtainium' };
    await userEvent.click(screen.getByRole('button', { name: 'assist-fill_medication_form' }));

    await waitFor(() => expect(toast.info).toHaveBeenCalled());
    expect(String(vi.mocked(toast.info).mock.calls[0][0])).toContain('Unobtainium');
    expect(screen.getByRole('button', { name: 'save_allergy' })).toBeDisabled();
  });

  it('picking a catalog allergen syncs the patient-level category from the entry', async () => {
    searchCatalogsMock.mockResolvedValue({
      results: [{ type: 'allergy', id: 'a1', label: 'Peanut' }],
    });
    getCatalogAllergy.mockResolvedValue({ id: 'a1', category: 'FOOD' });
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const { container } = render(
      <AllergyForm patientId="p1" onSubmit={onSubmit} />,
    );

    const input = screen.getByRole('combobox', { name: 'search_placeholder' });
    fireEvent.change(input, { target: { value: 'pea' } });
    const option = await screen.findByRole('option', { name: /Peanut/ });
    fireEvent.click(option);

    await waitFor(() => expect(screen.getByText('Peanut')).toBeInTheDocument());
    // The category (rendered nowhere — it travels in the form state) was
    // synced: the submit payload carries the catalog category.
    const save = screen.getByRole('button', { name: 'save_allergy' });
    await waitFor(() => expect(save).toBeEnabled());
    await userEvent.click(save);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const payload = onSubmit.mock.calls[0][0] as AllergyFormPayload;
    expect(payload.code).toEqual({ text: 'Peanut', catalog_id: 'a1' });
    expect(payload.category).toBe('FOOD');
    expect(payload).not.toHaveProperty('is_new_catalog_entry');
    expect(container).toBeInTheDocument();
  });
});
