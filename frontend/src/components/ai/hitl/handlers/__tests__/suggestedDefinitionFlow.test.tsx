/**
 * HITL unmatched proposals — the persistent chip + the shared create dialog
 * (plan C4 / C5 items 7–8).
 *
 * Covers:
 *   1. Medication `is_new`: the chip opens CreateCatalogItemModal prefilled
 *      with the proposal's DEFINITION fields + links only (no patient-instance
 *      text in the catalog payload); the create persists the links on the new
 *      definition; the created row becomes the form's `catalogSelection`; the
 *      proposed payload fields survive; edits made before AND after the dialog
 *      survive (prefill computed once); confirm does NOT re-persist the links
 *      the dialog already wrote.
 *   2. Cancel keeps the chip; Reject still records `dismissed`.
 *   3. A matched proposal renders NO chip.
 *   4. Allergy `is_new`: name/category/typical_reactions prefill (no
 *      `description` — not in the add payload); links + note survive.
 *   5. Add-biomarker `matched: false`: chip → create → the form hydrates the
 *      definition (selectedBiomarker → Save enabled) while the proposed
 *      value/note survive; the definition's preferred unit overwrites the
 *      proposed one; the confirm payload targets the created definition.
 *   6. Three-layer Escape: create dialog → browse → the record modal (the
 *      HITL popup yields to nested Radix dialogs).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { TaskInfo } from '../../../../../types/ai';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, dftOrOpts?: unknown, opts?: Record<string, unknown>) => {
      let s =
        typeof dftOrOpts === 'string'
          ? dftOrOpts
          : ((dftOrOpts as { defaultValue?: string })?.defaultValue ??
            k.split('.').pop() ??
            k);
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

const listCatalogTypesMock = vi.fn();
const createCatalogItemMock = vi.fn();
const searchCatalogsMock = vi.fn().mockResolvedValue({ results: [] });
const listCatalogItemsMock = vi.fn().mockResolvedValue({ items: [], total: 0 });

vi.mock('../../../../../services/catalogService', () => ({
  searchCatalogs: (...args: unknown[]) => searchCatalogsMock(...args),
  listCatalogItems: (...args: unknown[]) => listCatalogItemsMock(...args),
  listCatalogTypes: (...args: unknown[]) => listCatalogTypesMock(...args),
  createCatalogItem: (...args: unknown[]) => createCatalogItemMock(...args),
  getCatalogItem: vi.fn().mockResolvedValue({}),
  getCatalogGraph: vi.fn(async () => ({ nodes: [], edges: [] })),
  getRelationType: vi.fn(),
  getRelationTypes: vi.fn((): string[] => []),
  loadRelationTypes: vi.fn(async (): Promise<string[]> => []),
}));

const getAllBiomarkersMock = vi.fn().mockResolvedValue([]);
const getBiomarkerByIdMock = vi.fn().mockResolvedValue(null);
const syncRangesMock = vi.fn().mockResolvedValue(undefined);

vi.mock('../../../../../services/biomarkerService', () => ({
  __esModule: true,
  default: {
    getUnits: vi.fn().mockResolvedValue([]),
    getStates: vi.fn().mockResolvedValue([]),
    getAllBiomarkers: (...args: unknown[]) => getAllBiomarkersMock(...args),
    getBiomarkerById: (...args: unknown[]) => getBiomarkerByIdMock(...args),
    syncReferenceRanges: (...args: unknown[]) => syncRangesMock(...args),
  },
}));

const addPatientMedicationMock = vi.fn().mockResolvedValue({ id: 'rec-med-1' });
vi.mock('../../../../../services/medicationService', () => ({
  addPatientMedication: (...args: unknown[]) => addPatientMedicationMock(...args),
  updatePatientMedication: vi.fn(),
}));

const getCatalogAllergyMock = vi.fn().mockResolvedValue(null);
const addPatientAllergyMock = vi.fn().mockResolvedValue({ id: 'rec-all-1' });
vi.mock('../../../../../services/allergyService', () => ({
  getCatalogAllergy: (...args: unknown[]) => getCatalogAllergyMock(...args),
  addPatientAllergy: (...args: unknown[]) => addPatientAllergyMock(...args),
  updatePatientAllergy: vi.fn(),
}));

const createObservationMock = vi.fn().mockResolvedValue({ id: 'obs-1' });
vi.mock('../../../../../services/observationService', () => ({
  createObservation: (...args: unknown[]) => createObservationMock(...args),
}));

const resolveHitlTaskMock = vi.fn().mockResolvedValue({});
vi.mock('../../../../../services/aiAssistanceService', () => ({
  resolveHitlTask: (...args: unknown[]) => resolveHitlTaskMock(...args),
}));

const createLinksForMock = vi.fn().mockResolvedValue([]);
vi.mock('../../../../../services/conceptEdges', () => ({
  getLinkSchema: vi.fn().mockResolvedValue([]),
  createLinksFor: (...args: unknown[]) => createLinksForMock(...args),
  selectionsToLinkInputs: (selections: Array<Record<string, unknown>>) =>
    selections
      .filter((s) => Boolean(s.relation))
      .map((s) => ({
        dst_type: s.type,
        dst_id: s.id,
        dst_label: s.label,
        relation: s.relation,
      })),
}));

vi.mock('../../../../ui/AIAssistButton', () => ({ AIAssistButton: () => null }));
vi.mock('../../../../ui/RichTextEditor', () => ({ RichTextEditor: () => <div /> }));

vi.mock('../../../../../store/slices/authSlice', () => ({
  useAuthStore: (sel: (s: { user: { id: string } | null }) => unknown) =>
    sel({ user: { id: 'u1' } }),
}));

import { CreateMedicationHandler } from '../CreateMedicationHandler';
import { CreateAllergyHandler } from '../CreateAllergyHandler';
import { AddBiomarkerHandler } from '../AddBiomarkerHandler';
import { HitlTaskModal } from '../../HitlTaskModal';
import { getHitlHandler } from '../../registry';

const TYPE_METAS = [
  {
    type: 'medication',
    ui: { label_key: 'catalogs.medication.label', icon: 'Pill', color: 'blue', admin_route: '/x' },
    has_concept_link: false,
    edge_endpoint_type: 'medication',
    search_columns: [],
    write: {
      create_fields: ['name', 'description', 'indications', 'dosage_info', 'contraindications', 'side_effects'],
      update_fields: [],
      supports_promote: true,
    },
  },
  {
    type: 'allergy',
    ui: { label_key: 'catalogs.allergy.label', icon: 'ShieldAlert', color: 'amber', admin_route: '/x' },
    has_concept_link: false,
    edge_endpoint_type: 'allergy',
    search_columns: [],
    write: {
      create_fields: ['name', 'category', 'description', 'typical_reactions'],
      update_fields: [],
      supports_promote: true,
    },
  },
  {
    type: 'biomarker',
    ui: { label_key: 'catalogs.biomarker.label', icon: 'Activity', color: 'violet', admin_route: '/x' },
    has_concept_link: false,
    edge_endpoint_type: 'biomarker',
    search_columns: [],
    write: {
      create_fields: ['name', 'slug', 'coding_system', 'code', 'value_type'],
      update_fields: [],
      supports_promote: true,
    },
  },
];

const createdRow = (id: string, name: string) => ({
  id,
  name,
  slug: `server-derived-${id}`,
  seed_key: 'medication:curated',
  scope: 'user',
});

const medicationTask: TaskInfo = {
  schema_version: 1,
  proposal_id: 'prop-med-1',
  task_type: 'add_medication',
  title: 'Add Medication: Zolrix',
  status: 'proposed',
  proposed_payload: {
    // Identity / catalog resolution — unmatched.
    name: 'Zolrix',
    catalog_id: null,
    matched: false,
    is_new: true,
    // Catalog detail snapshot (definition fields).
    indications: '<p>acute pain</p>',
    side_effects: ['Nausea'],
    contraindications: '<p>pregnancy</p>',
    dosage_info: 'up to 3x daily',
    // Prescription (patient-instance) fields.
    dosage: '2 tablets',
    frequency_label: 'twice daily',
    reason: 'back pain',
    note: 'patient prefers syrup',
    start_date: '2026-11-01',
    status: 'active',
    links: [{ dst: { type: 'concept', id: 'c1', label: 'Pain' }, relation: 'TREATS' }],
  },
  context: { patient_id: 'p1' },
};

const allergyTask: TaskInfo = {
  schema_version: 1,
  proposal_id: 'prop-all-1',
  task_type: 'add_allergy',
  title: 'Add Allergy: Glutenex',
  status: 'proposed',
  proposed_payload: {
    name: 'Glutenex',
    catalog_id: null,
    matched: false,
    is_new: true,
    category: 'FOOD',
    typical_reactions: ['Hives', 'Itching'],
    clinical_status: 'ACTIVE',
    criticality: 'HIGH',
    verification_status: 'confirmed',
    onset_date: '2020-01-01',
    note: 'since childhood',
    links: [{ dst: { type: 'concept', id: 'c2', label: 'Gluten' }, relation: 'CAUSES' }],
  },
  context: { patient_id: 'p1' },
};

const biomarkerTask: TaskInfo = {
  schema_version: 1,
  proposal_id: 'prop-bio-1',
  task_type: 'add_biomarker_to_examination',
  title: 'Add Biomarker: Zylatrol = 4.2',
  status: 'proposed',
  proposed_payload: {
    biomarker_id: null,
    biomarker_name: 'Zylatrol',
    biomarker_slug: null,
    value: 4.2,
    unit: 'mg/L',
    note: 'fasting sample',
    matched: false,
  },
  context: {
    patient_id: 'p1',
    examination_id: 'exam-1',
    examination_category: 'lab',
    examination_date: '2026-11-01T09:00:00Z',
  },
};

/** Open the chip's create dialog and save it (the unified service returns the row). */
const defineThroughDialog = async (expectedName: string) => {
  await userEvent.click(screen.getByTestId('suggested-definition-chip'));
  const dialog = await screen.findByRole('dialog');
  await waitFor(() =>
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeEnabled(),
  );
  expect(within(dialog).getByDisplayValue(expectedName)).toBeInTheDocument();
  await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
  await waitFor(() =>
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
  );
};

beforeEach(() => {
  vi.clearAllMocks();
  searchCatalogsMock.mockResolvedValue({ results: [] });
  listCatalogItemsMock.mockResolvedValue({ items: [], total: 0 });
  getAllBiomarkersMock.mockResolvedValue([]);
  getBiomarkerByIdMock.mockResolvedValue(null);
  listCatalogTypesMock.mockResolvedValue({ types: TYPE_METAS });
  createCatalogItemMock.mockImplementation(async (type: string) =>
    createdRow(`row-${type}`, 'Zolrix'),
  );
  // Realistic default: one result per attempted link, all ok (per-test
  // overrides simulate failures with mockResolvedValueOnce).
  createLinksForMock.mockImplementation(
    async (
      _src: string,
      _srcId: string,
      links: Array<{ dst_type: string; dst_id: string; dst_label?: string; relation: string }>,
    ) =>
      links.map((l) => ({
        ok: true,
        dst: { type: l.dst_type, id: l.dst_id, label: l.dst_label ?? '' },
        relation: l.relation,
        edge_id: `edge-${l.dst_id}`,
      })),
  );
});

describe('CreateMedicationHandler — is_new chip → create dialog', () => {
  it('prefills the dialog with definition fields + links only, selects the created row, and keeps the proposed payload', async () => {
    const onResolved = vi.fn();
    render(
      <CreateMedicationHandler
        task={medicationTask}
        sessionId="s1"
        onResolved={onResolved}
        onCancel={vi.fn()}
      />,
    );

    // The chip is persistent above the form; Save is gated on a selection.
    expect(screen.getByTestId('suggested-definition-chip')).toHaveTextContent('Zolrix');
    const save = screen.getByRole('button', { name: 'Confirm & Add Medication' });
    expect(save).toBeDisabled();

    // Edit BEFORE the dialog — must survive the whole round-trip.
    const dosage = screen.getByPlaceholderText('dosage_placeholder');
    await userEvent.clear(dosage);
    await userEvent.type(dosage, '3 tablets');

    // Define the suggested item through the shared dialog.
    await defineThroughDialog('Zolrix');

    // The dialog created the DEFINITION through the unified service —
    // definition fields in, patient-instance text out.
    expect(createCatalogItemMock).toHaveBeenCalledTimes(1);
    const [, payload] = createCatalogItemMock.mock.calls[0] as [string, Record<string, unknown>];
    expect(payload).toMatchObject({
      name: 'Zolrix',
      indications: '<p>acute pain</p>',
      side_effects: ['Nausea'],
      contraindications: '<p>pregnancy</p>',
      dosage_info: 'up to 3x daily',
    });
    expect(payload).not.toHaveProperty('dosage');
    expect(payload).not.toHaveProperty('reason');
    expect(payload).not.toHaveProperty('note');
    expect(payload).not.toHaveProperty('start_date');
    expect(payload).not.toHaveProperty('frequency_label');

    // The proposed links were persisted on the created definition by the dialog.
    expect(createLinksForMock).toHaveBeenCalledTimes(1);
    expect(createLinksForMock).toHaveBeenCalledWith(
      'medication',
      'row-medication',
      [{ dst_type: 'concept', dst_id: 'c1', dst_label: 'Pain', relation: 'TREATS' }],
      expect.objectContaining({ source: 'manual' }),
    );

    // The created row became the form's selection; the proposed fields and
    // the pre-dialog edit survived (prefill computed once).
    expect(screen.getByText('Zolrix')).toBeInTheDocument();
    expect(save).toBeEnabled();
    expect(screen.getByDisplayValue('3 tablets')).toBeInTheDocument();
    expect(screen.getByDisplayValue('patient prefers syrup')).toBeInTheDocument();

    // Edit AFTER the dialog, then confirm.
    const dosageAfter = screen.getByPlaceholderText('dosage_placeholder');
    await userEvent.clear(dosageAfter);
    await userEvent.type(dosageAfter, '4 tablets');
    await userEvent.click(save);

    await waitFor(() => expect(addPatientMedicationMock).toHaveBeenCalledTimes(1));
    expect(addPatientMedicationMock).toHaveBeenCalledWith(
      'p1',
      expect.objectContaining({
        dosage: '4 tablets',
        reason: 'back pain',
        note: 'patient prefers syrup',
        code: { text: 'Zolrix', catalog_id: 'row-medication' },
      }),
    );
    // Confirm did NOT re-persist the dialog-written links (no duplicate edges).
    expect(createLinksForMock).toHaveBeenCalledTimes(1);
    expect(resolveHitlTaskMock).toHaveBeenCalledWith(
      's1',
      'prop-med-1',
      expect.objectContaining({ status: 'confirmed' }),
    );
    await waitFor(() =>
      expect(onResolved.mock.calls[0][0]).toMatchObject({ status: 'confirmed' }),
    );
  });

  it('retries exactly the dialog-failed link at confirm (persistedLinks delta, S2)', async () => {
    const twoLinkTask: TaskInfo = {
      ...medicationTask,
      proposed_payload: {
        ...medicationTask.proposed_payload,
        links: [
          { dst: { type: 'concept', id: 'c1', label: 'Pain' }, relation: 'TREATS' },
          { dst: { type: 'concept', id: 'c3', label: 'Nausea' }, relation: 'CAUSES' },
        ],
      },
    };
    // The dialog's link step: the first link persists, the second FAILS.
    createLinksForMock.mockResolvedValueOnce([
      {
        ok: true,
        dst: { type: 'concept', id: 'c1', label: 'Pain' },
        relation: 'TREATS',
        edge_id: 'e1',
      },
      {
        ok: false,
        dst: { type: 'concept', id: 'c3', label: 'Nausea' },
        relation: 'CAUSES',
        error: 'boom',
      },
    ]);
    // The confirm retry succeeds.
    createLinksForMock.mockResolvedValueOnce([
      {
        ok: true,
        dst: { type: 'concept', id: 'c3', label: 'Nausea' },
        relation: 'CAUSES',
        edge_id: 'e2',
      },
    ]);
    const onResolved = vi.fn();
    render(
      <CreateMedicationHandler
        task={twoLinkTask}
        sessionId="s1"
        onResolved={onResolved}
        onCancel={vi.fn()}
      />,
    );

    await defineThroughDialog('Zolrix');
    // The dialog attempted BOTH links on the created definition.
    expect(createLinksForMock).toHaveBeenCalledTimes(1);
    expect(createLinksForMock.mock.calls[0][2]).toEqual([
      { dst_type: 'concept', dst_id: 'c1', dst_label: 'Pain', relation: 'TREATS' },
      { dst_type: 'concept', dst_id: 'c3', dst_label: 'Nausea', relation: 'CAUSES' },
    ]);

    // Confirm re-persists ONLY the failed link — the succeeded one is
    // seeded into the delta-ref via onCreated's meta.persistedLinks.
    await userEvent.click(screen.getByRole('button', { name: 'Confirm & Add Medication' }));
    await waitFor(() => expect(addPatientMedicationMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(createLinksForMock).toHaveBeenCalledTimes(2));
    expect(createLinksForMock.mock.calls[1][2]).toEqual([
      { dst_type: 'concept', dst_id: 'c3', dst_label: 'Nausea', relation: 'CAUSES' },
    ]);
    expect(resolveHitlTaskMock).toHaveBeenCalledWith(
      's1',
      'prop-med-1',
      expect.objectContaining({
        status: 'confirmed',
        result: expect.objectContaining({ links_failed: 0 }),
      }),
    );
    await waitFor(() =>
      expect(onResolved.mock.calls[0][0]).toMatchObject({ status: 'confirmed' }),
    );
  });

  it('cancel keeps the chip; Reject records dismissed', async () => {
    const onResolved = vi.fn();
    render(
      <CreateMedicationHandler
        task={medicationTask}
        sessionId="s1"
        onResolved={onResolved}
        onCancel={vi.fn()}
      />,
    );

    await userEvent.click(screen.getByTestId('suggested-definition-chip'));
    await screen.findByRole('dialog');
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    // The chip survives the cancel; nothing was created or resolved.
    expect(screen.getByTestId('suggested-definition-chip')).toBeInTheDocument();
    expect(createCatalogItemMock).not.toHaveBeenCalled();
    expect(resolveHitlTaskMock).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: 'Reject' }));
    expect(resolveHitlTaskMock).toHaveBeenCalledWith('s1', 'prop-med-1', {
      status: 'dismissed',
    });
    await waitFor(() =>
      expect(onResolved.mock.calls[0][0]).toMatchObject({ status: 'dismissed' }),
    );
  });

  it('renders no chip for a matched proposal', () => {
    render(
      <CreateMedicationHandler
        task={{
          ...medicationTask,
          proposed_payload: {
            ...medicationTask.proposed_payload,
            is_new: false,
            matched: true,
            catalog_id: 'm-existing',
            links: [],
          },
        }}
        sessionId="s1"
        onResolved={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('suggested-definition-chip')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirm & Add Medication' })).toBeEnabled();
  });
});

describe('CreateAllergyHandler — is_new chip → create dialog', () => {
  it('prefills name/category/typical_reactions (no description), persists links, and the note survives', async () => {
    createCatalogItemMock.mockResolvedValue(createdRow('row-allergy', 'Glutenex'));
    // N2: the programmatic pick syncs the category from the created row —
    // the same lookup a manual pick runs.
    getCatalogAllergyMock.mockResolvedValue({ id: 'row-allergy', category: 'MEDICATION' });
    const onResolved = vi.fn();
    render(
      <CreateAllergyHandler
        task={allergyTask}
        sessionId="s1"
        onResolved={onResolved}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByTestId('suggested-definition-chip')).toHaveTextContent('Glutenex');
    await defineThroughDialog('Glutenex');

    // Definition fields only — no `description` (not in the add payload), no
    // patient-instance text.
    const [, payload] = createCatalogItemMock.mock.calls[0] as [string, Record<string, unknown>];
    expect(payload).toMatchObject({
      name: 'Glutenex',
      category: 'FOOD',
      typical_reactions: ['Hives', 'Itching'],
    });
    expect(payload).not.toHaveProperty('description');
    expect(payload).not.toHaveProperty('note');
    expect(payload).not.toHaveProperty('onset_date');
    expect(payload).not.toHaveProperty('criticality');

    // The proposed links were persisted on the created allergen by the dialog.
    expect(createLinksForMock).toHaveBeenCalledTimes(1);
    expect(createLinksForMock).toHaveBeenCalledWith(
      'allergy',
      'row-allergy',
      [{ dst_type: 'concept', dst_id: 'c2', dst_label: 'Gluten', relation: 'CAUSES' }],
      expect.objectContaining({ source: 'manual' }),
    );

    // The created row is selected; the proposed note survives; confirm does
    // not duplicate the links.
    expect(screen.getByText('Glutenex')).toBeInTheDocument();
    expect(screen.getByDisplayValue('since childhood')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Confirm & Add Allergy' }));

    await waitFor(() => expect(addPatientAllergyMock).toHaveBeenCalledTimes(1));
    expect(addPatientAllergyMock).toHaveBeenCalledWith(
      'p1',
      expect.objectContaining({
        note: 'since childhood',
        criticality: 'HIGH',
        // N2: the category was synced from the created catalog row (the
        // proposed FOOD is overridden exactly like on a manual pick).
        category: 'MEDICATION',
        code: { text: 'Glutenex', catalog_id: 'row-allergy' },
      }),
    );
    expect(createLinksForMock).toHaveBeenCalledTimes(1);
    expect(resolveHitlTaskMock).toHaveBeenCalledWith(
      's1',
      'prop-all-1',
      expect.objectContaining({ status: 'confirmed' }),
    );
  });
});

describe('AddBiomarkerHandler — matched:false chip → create dialog', () => {
  const BIOMARKER_DEFINITION = {
    id: 'row-biomarker',
    name: 'Zylatrol',
    slug: 'zylatrol',
    value_type: 'numeric',
    preferred_unit_symbol: 'ng/mL',
    coding_system: 'custom',
    allowed_states: [],
  };

  it('after create the form hydrates the definition and the proposed value/note survive', async () => {
    createCatalogItemMock.mockResolvedValue(createdRow('row-biomarker', 'Zylatrol'));
    getBiomarkerByIdMock.mockResolvedValue(BIOMARKER_DEFINITION);
    const onResolved = vi.fn();
    render(
      <AddBiomarkerHandler
        task={biomarkerTask}
        sessionId="s1"
        onResolved={onResolved}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByTestId('suggested-definition-chip')).toHaveTextContent('Zylatrol');
    // Unmatched proposal → no hydration happened yet → Save disabled.
    const save = screen.getByRole('button', { name: 'Confirm & Add' });
    expect(save).toBeDisabled();

    await defineThroughDialog('Zylatrol');

    // The created definition hydrates the form (selectedBiomarker) — Save
    // enabled — while the proposed value/note survive. The definition's
    // preferred unit overwrites the proposed one.
    expect(getBiomarkerByIdMock).toHaveBeenCalledWith('row-biomarker');
    expect(save).toBeEnabled();
    expect(screen.getByDisplayValue('4.2')).toBeInTheDocument();
    expect(screen.getByDisplayValue('fasting sample')).toBeInTheDocument();

    await userEvent.click(save);
    await waitFor(() => expect(createObservationMock).toHaveBeenCalledTimes(1));
    const observation = createObservationMock.mock.calls[0][0] as Record<string, any>;
    expect(observation.biomarker_id).toBe('row-biomarker');
    expect(observation.examination_id).toBe('exam-1');
    expect(observation.value_quantity).toMatchObject({ value: 4.2, unit: 'ng/mL' });
    expect(observation.note).toEqual([{ text: 'fasting sample' }]);
    expect(resolveHitlTaskMock).toHaveBeenCalledWith(
      's1',
      'prop-bio-1',
      expect.objectContaining({ status: 'confirmed' }),
    );
    await waitFor(() =>
      expect(onResolved.mock.calls[0][0]).toMatchObject({ status: 'confirmed' }),
    );
  });
});

describe('three-layer Escape (create dialog → browse → HITL record modal)', () => {
  it('unwinds one layer at a time', async () => {
    const handler = getHitlHandler('add_biomarker_to_examination');
    expect(handler).toBeTruthy();
    const onClose = vi.fn();
    render(
      <HitlTaskModal
        isOpen
        onClose={onClose}
        task={biomarkerTask}
        sessionId="s1"
        handler={handler!}
        onResolved={vi.fn()}
      />,
    );

    // Layer 1: the record modal (HITL popup) hosting the form.
    expect(screen.getByText('Add Biomarker: Zylatrol = 4.2')).toBeInTheDocument();

    // Layer 2: the picker's browse modal.
    fireEvent.click(screen.getByRole('button', { name: 'Browse' }));
    const browse = await screen.findByRole('dialog');

    // Layer 3: the create dialog from the browse header's New action. The
    // lower dialog is aria-hidden while the top one is open, so count with
    // `hidden: true` (DOM order: browse first, create second).
    fireEvent.click(within(browse).getByRole('button', { name: 'New Biomarker' }));
    await waitFor(() =>
      expect(screen.getAllByRole('dialog', { hidden: true })).toHaveLength(2),
    );

    // Escape closes ONLY the create dialog.
    const [, createDialog] = screen.getAllByRole('dialog', { hidden: true });
    fireEvent.keyDown(createDialog, { key: 'Escape' });
    await waitFor(() =>
      expect(screen.getAllByRole('dialog', { hidden: true })).toHaveLength(1),
    );
    expect(onClose).not.toHaveBeenCalled();

    // Escape closes the browse modal; the record modal stays.
    fireEvent.keyDown(screen.getAllByRole('dialog', { hidden: true })[0], {
      key: 'Escape',
    });
    await waitFor(() =>
      expect(screen.queryAllByRole('dialog', { hidden: true })).toHaveLength(0),
    );
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByText('Add Biomarker: Zylatrol = 4.2')).toBeInTheDocument();

    // Escape (no nested dialog left) closes the record modal.
    fireEvent.keyDown(document.body, { key: 'Escape' });
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });
});
