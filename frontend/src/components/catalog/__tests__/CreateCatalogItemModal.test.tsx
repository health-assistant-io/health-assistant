/**
 * CreateCatalogItemModal tests (the one manual definition surface).
 *
 * Covers:
 *   1. Every type creates through the unified generic service — one contract,
 *      no per-type branching; the payload carries only the type's allowlisted
 *      create fields and never a client-sent `seed_key` or `slug` (identity is
 *      server-owned; ADR 0055 D8 — the backend derives the biomarker slug from
 *      the name when the payload omits it, so the dialog passes
 *      `serverOwnedSlug` and the form renders the slug field disabled instead
 *      of advertising an edit that would be silently dropped).
 *   2. Biomarker draft ranges are persisted via syncReferenceRanges after the
 *      create (the sync is the single range writer — same as the workspace).
 *      A FAILING sync never re-creates the row (S1): the create service runs
 *      once, the created row is still reported through onCreated, and the
 *      dialog closes with a distinct toast.
 *   3. Links (draft + initialLinks) persist via createLinksFor with the type
 *      descriptor's edge_endpoint_type; onCreated's meta carries ONLY the
 *      links that actually persisted (S2), so a failed link is retried at
 *      confirm time instead of being dropped.
 *   4. Errors toast the backend's first readable message (a 422 array `detail`
 *      renders as `field: msg`) and keep the dialog open; success reports the
 *      created row (server slug/seed_key) through onCreated and closes.
 *   5. Empty name blocks Save; the draft resets on every open; a failed type
 *      metadata load shows an inline message instead of a dead Save button.
 *   6. The AI definition assist is medication-only (allergy and biomarker
 *      have none), mapped 1:1 onto the draft.
 */
import React, { useState } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, dftOrOpts?: unknown, opts?: Record<string, unknown>) => {
      let s =
        typeof dftOrOpts === 'string'
          ? dftOrOpts
          : ((dftOrOpts as { defaultValue?: string })?.defaultValue ?? k);
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

const listCatalogTypesMock = vi.fn();
const createCatalogItemMock = vi.fn();
const syncRangesMock = vi.fn();
const createLinksForMock = vi.fn();

vi.mock('react-toastify', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
  },
}));

vi.mock('../../../services/catalogService', () => ({
  listCatalogTypes: (...args: unknown[]) => listCatalogTypesMock(...args),
  createCatalogItem: (...args: unknown[]) => createCatalogItemMock(...args),
  updateCatalogItem: vi.fn(),
  deleteCatalogItem: vi.fn(),
  listCatalogItems: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  searchCatalogs: vi.fn().mockResolvedValue({ results: [] }),
  getCatalogItem: vi.fn(),
  getRelationType: vi.fn(),
  getRelationTypes: vi.fn(() => []),
  loadRelationTypes: vi.fn(async () => []),
}));

vi.mock('../../../services/biomarkerService', () => ({
  __esModule: true,
  default: {
    getUnits: vi.fn().mockResolvedValue([]),
    getStates: vi.fn().mockResolvedValue([]),
    syncReferenceRanges: (...args: unknown[]) => syncRangesMock(...args),
  },
}));

vi.mock('../../../services/conceptEdges', () => ({
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

vi.mock('../../ui/AIAssistButton', () => ({
  AIAssistButton: ({
    onSuggestedData,
  }: {
    onSuggestedData: (data: Record<string, unknown>) => void;
  }) => (
    <button
      type="button"
      onClick={() =>
        onSuggestedData({
          name: 'Suggested Med',
          description: '<p>AI description</p>',
          indications: '<p>AI indications</p>',
          dosage_info: '500mg',
          contraindications: '<p>AI contraindications</p>',
          side_effects: ['Nausea'],
        })
      }
    >
      ai-assist
    </button>
  ),
}));

vi.mock('../../ui/RichTextEditor', () => ({
  RichTextEditor: () => <div data-testid="rich-editor" />,
}));

vi.mock('../../../store/slices/authSlice', () => ({
  useAuthStore: (sel: (s: { user: { id: string } | null }) => unknown) =>
    sel({ user: { id: 'u1' } }),
}));

import { CreateCatalogItemModal } from '../CreateCatalogItemModal';
import { toast } from 'react-toastify';
import type { CatalogSelection } from '../../../types/catalog';

const TYPE_METAS = [
  {
    type: 'medication',
    ui: { label_key: 'catalogs.medication.label', icon: 'Pill', color: 'blue', admin_route: '/x' },
    has_concept_link: false,
    edge_endpoint_type: 'medication',
    search_columns: [],
    write: { create_fields: ['name', 'description', 'indications', 'dosage_info', 'contraindications', 'side_effects'], update_fields: [], supports_promote: true },
  },
  {
    type: 'allergy',
    ui: { label_key: 'catalogs.allergy.label', icon: 'ShieldAlert', color: 'amber', admin_route: '/x' },
    has_concept_link: false,
    edge_endpoint_type: 'allergy',
    search_columns: [],
    write: { create_fields: ['name', 'category', 'description', 'typical_reactions'], update_fields: [], supports_promote: true },
  },
  {
    type: 'vaccine',
    ui: { label_key: 'catalogs.vaccine.label', icon: 'Syringe', color: 'rose', admin_route: '/x' },
    has_concept_link: false,
    edge_endpoint_type: 'immunization',
    search_columns: [],
    write: { create_fields: ['name', 'description', 'side_effects'], update_fields: [], supports_promote: true },
  },
  {
    type: 'biomarker',
    ui: { label_key: 'catalogs.biomarker.label', icon: 'Activity', color: 'violet', admin_route: '/x' },
    has_concept_link: false,
    edge_endpoint_type: 'biomarker',
    search_columns: [],
    write: { create_fields: ['name', 'slug', 'coding_system', 'code', 'value_type'], update_fields: [], supports_promote: true },
  },
];

const createdRow = (type: string) => ({
  id: `row-${type}`,
  name: 'Aspirin',
  slug: `server-derived-${type}`,
  seed_key: `${type}:curated`,
  scope: 'user',
});

const renderModal = (props: Partial<Parameters<typeof CreateCatalogItemModal>[0]> = {}) => {
  const onCreated = vi.fn();
  const onClose = vi.fn();
  render(
    <CreateCatalogItemModal
      isOpen
      type="medication"
      onClose={onClose}
      onCreated={onCreated}
      {...props}
    />,
  );
  return { onCreated, onClose };
};

const nameInput = (): HTMLInputElement =>
  screen
    .getAllByRole('textbox')
    .find((el) => (el as HTMLInputElement).type === 'text') as HTMLInputElement;

const typeNameAndSave = async (name: string) => {
  const input = nameInput();
  await userEvent.clear(input);
  if (name) await userEvent.type(input, name);
  await userEvent.click(screen.getByRole('button', { name: 'Save' }));
};

describe('CreateCatalogItemModal unified create path', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listCatalogTypesMock.mockResolvedValue({ types: TYPE_METAS });
    createCatalogItemMock.mockImplementation(async (type: string) =>
      createdRow(type),
    );
    createLinksForMock.mockResolvedValue([]);
    syncRangesMock.mockResolvedValue(undefined);
  });

  it.each(['medication', 'allergy', 'vaccine'] as const)(
    '%s creates through the generic service with an allowlisted payload (no slug/seed_key)',
    async (type) => {
      const { onCreated, onClose } = renderModal({ type, initialName: 'Aspirin' });
      await typeNameAndSave('Aspirin');

      await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
      expect(createCatalogItemMock).toHaveBeenCalledTimes(1);
      const [sentType, payload] = createCatalogItemMock.mock.calls[0] as [
        string,
        Record<string, unknown>,
      ];
      expect(sentType).toBe(type);
      expect(payload).toMatchObject({ name: 'Aspirin' });
      expect(payload).not.toHaveProperty('slug');
      expect(payload).not.toHaveProperty('seed_key');
      expect(payload).not.toHaveProperty('id');
      expect(payload).not.toHaveProperty('scope');
      // The created row carries the SERVER-derived identity.
      expect(onCreated.mock.calls[0][0]).toMatchObject({
        id: `row-${type}`,
        slug: `server-derived-${type}`,
        seed_key: `${type}:curated`,
      });
      expect(onClose).toHaveBeenCalledTimes(1);
    },
  );

  it('biomarker never sends a slug — the backend derives it and the dialog closes on the server row', async () => {
    // Prefilled name, saved WITHOUT typing: the form's slug derivation never
    // ran, so the payload carries no slug and the backend derives one (the
    // mocked server row carries the derived identity).
    const { onCreated, onClose } = renderModal({
      type: 'biomarker',
      initialName: 'Glucose',
    });
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
    const [, payload] = createCatalogItemMock.mock.calls[0] as [
      string,
      Record<string, unknown>,
    ];
    expect(payload).toMatchObject({ name: 'Glucose' });
    expect(payload).not.toHaveProperty('slug');
    expect(payload).not.toHaveProperty('seed_key');
    // The server-derived identity is what the caller receives.
    expect(onCreated.mock.calls[0][0]).toMatchObject({
      id: 'row-biomarker',
      slug: 'server-derived-biomarker',
    });
    expect(onClose).toHaveBeenCalledTimes(1);

    // A TYPED name derives the slug for display only — still never sent.
    createCatalogItemMock.mockClear();
    onCreated.mockClear();
    await typeNameAndSave('Fasting Glucose');
    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
    const [, typedPayload] = createCatalogItemMock.mock.calls[0] as [
      string,
      Record<string, unknown>,
    ];
    expect(typedPayload).toMatchObject({ name: 'Fasting Glucose' });
    expect(typedPayload).not.toHaveProperty('slug');
  });

  it('renders the biomarker slug field disabled (server-owned) and the payload stays slugless', async () => {
    const { onCreated, onClose } = renderModal({
      type: 'biomarker',
      initialName: 'Glucose',
    });
    // The dialog passes serverOwnedSlug: no editable slug field to silently
    // discard — editing is impossible, the hint says the server derives it.
    const slug = screen.getByPlaceholderText(
      'auto-generated-from-name',
    ) as HTMLInputElement;
    expect(slug).toBeDisabled();
    expect(
      screen.queryByText('Auto-generated from name — edit to override'),
    ).not.toBeInTheDocument();

    await typeNameAndSave('Fasting Glucose');
    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
    const [, payload] = createCatalogItemMock.mock.calls[0] as [
      string,
      Record<string, unknown>,
    ];
    expect(payload).toMatchObject({ name: 'Fasting Glucose' });
    expect(payload).not.toHaveProperty('slug');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('a throwing onCreated is never invoked a second time (reported flag flips first)', async () => {
    const onCreated = vi.fn(() => {
      throw new Error('caller blew up');
    });
    render(
      <CreateCatalogItemModal
        isOpen
        type="medication"
        initialName="Aspirin"
        onClose={vi.fn()}
        onCreated={onCreated}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    // Exactly ONE report for the one created row — the old order (flag set
    // after the call) let a throwing caller fall into the catch's re-report.
    expect(onCreated).toHaveBeenCalledTimes(1);
    expect(createCatalogItemMock).toHaveBeenCalledTimes(1);
  });

  it('a failing ranges sync never re-creates the row: create runs once, onCreated still fires (S1)', async () => {
    syncRangesMock.mockRejectedValue(new Error('range sync down'));
    const { onCreated, onClose } = renderModal({
      type: 'biomarker',
      initialName: 'Glucose',
      initialDraft: { reference_ranges: [{ sex: 'male', min: 4, max: 6 }] },
    });
    await typeNameAndSave('Glucose');

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringContaining('Saved, but'),
      ),
    );
    // The create ran EXACTLY once — the retry-a-duplicate path is gone.
    expect(createCatalogItemMock).toHaveBeenCalledTimes(1);
    // The created row is still reported so the caller binds to it, and the
    // dialog closes (the item exists server-side).
    expect(onCreated).toHaveBeenCalledTimes(1);
    expect(onCreated.mock.calls[0][0]).toMatchObject({ id: 'row-biomarker' });
    expect(onCreated.mock.calls[0][1]).toMatchObject({ persistedLinks: [] });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('onCreated meta.persistedLinks carries only the links that actually persisted (S2)', async () => {
    createLinksForMock.mockResolvedValue([
      {
        ok: true,
        dst: { type: 'concept', id: 'c1', label: 'Panel' },
        relation: 'MEMBER_OF',
        edge_id: 'e1',
      },
      {
        ok: false,
        dst: { type: 'anatomy', id: 'a1', label: 'Blood' },
        relation: 'MEASURED_AT',
        error: '409',
      },
    ]);
    const { onCreated } = renderModal({
      type: 'biomarker',
      initialName: 'Glucose',
      initialDraft: {
        links: [
          { type: 'concept', id: 'c1', label: 'Panel', relation: 'MEMBER_OF' },
          { type: 'anatomy', id: 'a1', label: 'Blood', relation: 'MEASURED_AT' },
        ],
      },
    });
    await typeNameAndSave('Glucose');

    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
    expect(createLinksForMock).toHaveBeenCalledTimes(1);
    const meta = onCreated.mock.calls[0][1] as { persistedLinks: CatalogSelection[] };
    expect(meta.persistedLinks).toEqual([
      { type: 'concept', id: 'c1', label: 'Panel', relation: 'MEMBER_OF' },
    ]);
  });

  it('syncs the biomarker draft ranges after create and persists links via the edge endpoint type', async () => {
    const ranges = [{ sex: 'male', min: 4, max: 6, unit_id: 'u1' }];
    renderModal({
      type: 'biomarker',
      initialName: 'Glucose',
      initialDraft: {
        reference_ranges: ranges,
        links: [{ type: 'concept', id: 'c1', label: 'Panel', relation: 'MEMBER_OF' }],
      },
      initialLinks: [{ type: 'anatomy', id: 'a1', label: 'Blood', relation: 'MEASURED_AT' }],
    });
    await typeNameAndSave('Glucose');

    await waitFor(() =>
      expect(syncRangesMock).toHaveBeenCalledWith('row-biomarker', ranges),
    );
    expect(createLinksForMock).toHaveBeenCalledWith(
      'biomarker',
      'row-biomarker',
      expect.arrayContaining([
        expect.objectContaining({ dst_id: 'c1', relation: 'MEMBER_OF' }),
        expect.objectContaining({ dst_id: 'a1', relation: 'MEASURED_AT' }),
      ]),
      expect.objectContaining({ source: 'manual' }),
    );
  });

  it('medication links persist through the medication edge endpoint type', async () => {
    renderModal({
      initialDraft: {
        links: [{ type: 'concept', id: 'd1', label: 'Headache', relation: 'TREATS' }],
      },
    });
    await typeNameAndSave('Aspirin');

    await waitFor(() =>
      expect(createLinksForMock).toHaveBeenCalledWith(
        'medication',
        'row-medication',
        [expect.objectContaining({ dst_id: 'd1', relation: 'TREATS' })],
        expect.anything(),
      ),
    );
  });

  it('empty name blocks Save', async () => {
    renderModal({ type: 'medication' });
    const save = screen.getByRole('button', { name: 'Save' });
    expect(save).toBeDisabled();
    await userEvent.type(nameInput(), 'Aspirin');
    await waitFor(() => expect(save).toBeEnabled());
    expect(createCatalogItemMock).not.toHaveBeenCalled();
  });

  it('error toast shows the backend detail and the dialog stays open', async () => {
    createCatalogItemMock.mockRejectedValue({
      response: { data: { detail: 'name already exists' } },
    });
    const { onClose } = renderModal({ initialName: 'Aspirin' });
    await typeNameAndSave('Aspirin');

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('name already exists'),
    );
    expect(onClose).not.toHaveBeenCalled();
  });

  it('error toast falls back to the generic message when the backend has no detail', async () => {
    createCatalogItemMock.mockRejectedValue(new Error('network down'));
    const { onClose } = renderModal({ initialName: 'Aspirin' });
    await typeNameAndSave('Aspirin');

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('Could not save'),
    );
    expect(onClose).not.toHaveBeenCalled();
  });

  it('a 422 array detail renders as a readable first message (field: msg)', async () => {
    createCatalogItemMock.mockRejectedValue({
      response: {
        status: 422,
        data: {
          detail: [
            { loc: ['body', 'name'], msg: 'Field required', type: 'missing' },
            { loc: ['body', 'code'], msg: 'Field required', type: 'missing' },
          ],
        },
      },
    });
    const { onClose } = renderModal({ initialName: 'Aspirin' });
    await typeNameAndSave('Aspirin');

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('name: Field required'),
    );
    expect(onClose).not.toHaveBeenCalled();
  });

  it('a failed type-metadata load shows an inline message instead of a dead Save button', async () => {
    listCatalogTypesMock.mockRejectedValue(new Error('types down'));
    renderModal({ initialName: 'Aspirin' });

    // The failure is explained, not silent.
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'The form fields could not be loaded',
      ),
    );
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(createCatalogItemMock).not.toHaveBeenCalled();
  });

  it('resets the draft on every open (cancel → reopen shows the new initial values)', async () => {
    const Harness = () => {
      const [open, setOpen] = useState(true);
      const [name, setName] = useState('Aspirin');
      return (
        <>
          <button
            type="button"
            onClick={() => {
              setName('Renamed Initial');
              setOpen(true);
            }}
          >
            reopen
          </button>
          <CreateCatalogItemModal
            isOpen={open}
            type="medication"
            initialName={name}
            onClose={() => setOpen(false)}
            onCreated={vi.fn()}
          />
        </>
      );
    };
    render(<Harness />);

    const input = nameInput();
    expect(input).toHaveValue('Aspirin');
    await userEvent.clear(input);
    await userEvent.type(input, 'Typed Draft');
    expect(input).toHaveValue('Typed Draft');

    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    // Reopen with a different initial name — the stale draft must not survive.
    await userEvent.click(screen.getByRole('button', { name: 'reopen' }));
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());
    expect(nameInput()).toHaveValue('Renamed Initial');
  });

  it('AI definition assist is medication-only (allergy and biomarker have none)', async () => {
    const first = render(
      <CreateCatalogItemModal
        isOpen
        type="medication"
        initialName="Aspirin"
        onClose={vi.fn()}
        onCreated={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'ai-assist' })).toBeInTheDocument();

    first.unmount();
    render(
      <CreateCatalogItemModal
        isOpen
        type="allergy"
        initialName="Peanuts"
        onClose={vi.fn()}
        onCreated={vi.fn()}
      />,
    );
    expect(screen.queryByRole('button', { name: 'ai-assist' })).not.toBeInTheDocument();

    first.unmount();
    render(
      <CreateCatalogItemModal
        isOpen
        type="biomarker"
        initialName="Glucose"
        onClose={vi.fn()}
        onCreated={vi.fn()}
      />,
    );
    expect(screen.queryByRole('button', { name: 'ai-assist' })).not.toBeInTheDocument();
  });

  it('applies the medication AI suggestion 1:1 and sends it in the payload', async () => {
    renderModal({ initialName: '' });
    await userEvent.click(screen.getByRole('button', { name: 'ai-assist' }));
    // The suggestion fills the name too (it was empty).
    expect(nameInput()).toHaveValue('Suggested Med');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(createCatalogItemMock).toHaveBeenCalledTimes(1));
    const [, payload] = createCatalogItemMock.mock.calls[0] as [
      string,
      Record<string, unknown>,
    ];
    expect(payload).toMatchObject({
      name: 'Suggested Med',
      description: '<p>AI description</p>',
      indications: '<p>AI indications</p>',
      dosage_info: '500mg',
      contraindications: '<p>AI contraindications</p>',
      side_effects: ['Nausea'],
    });
  });
});
