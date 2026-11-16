/**
 * The workspace's one write path (plan A Phase 2 — ADR 0055 D5).
 *
 * Every catalog type — concept and anatomy included — must save/delete
 * through the generic `/catalogs/{type}` service functions with the
 * allowlist-filtered payload. The retired per-type write-target dispatch
 * (`writeTarget.ts` → `/concepts` + `/anatomy` domain writes) must never be
 * reached again, and restore stays concept-only.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import type { ReactElement } from 'react';
import type { CatalogTypeMeta } from '../../../types/catalog';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallbackOrOpts?: unknown) =>
      typeof fallbackOrOpts === 'string'
        ? fallbackOrOpts
        : (fallbackOrOpts as { defaultValue?: string } | undefined)?.defaultValue ?? key,
    i18n: { language: 'en' },
  }),
}));

vi.mock('react-toastify', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
  },
}));

vi.mock('../../../services/catalogService', () => ({
  listCatalogTypes: vi.fn(),
  listCatalogItems: vi.fn(),
  getCatalogItem: vi.fn(),
  createCatalogItem: vi.fn(),
  updateCatalogItem: vi.fn(),
  deleteCatalogItem: vi.fn(),
  promoteCatalogItem: vi.fn(),
  searchCatalogs: vi.fn().mockResolvedValue({ results: [] }),
  getCatalogRelations: vi.fn().mockResolvedValue({ start: null, nodes: [], edges: [] }),
  getCatalogGraph: vi.fn().mockResolvedValue({ nodes: [], edges: [], truncated: false }),
  getCatalogItemHistory: vi.fn().mockResolvedValue({ items: [] }),
  loadRelationTypes: vi.fn().mockResolvedValue([]),
  getRelationTypes: vi.fn().mockReturnValue([]),
  getRelationType: vi.fn().mockReturnValue(undefined),
  invalidateRelationTypes: vi.fn(),
}));

vi.mock('../../../services/conceptService', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../../services/conceptService')>();
  return {
    ...actual,
    createConcept: vi.fn(),
    updateConcept: vi.fn(),
    deleteConcept: vi.fn(),
    restoreConcept: vi.fn(),
    createEdge: vi.fn(),
    deleteEdge: vi.fn(),
  };
});

vi.mock('../../../services/anatomyService', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../../services/anatomyService')>();
  return {
    ...actual,
    anatomyService: {
      ...actual.anatomyService,
      create: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
    },
  };
});

// The forms pull heavy nested widgets (pickers, rich-text editors) the write
// path does not exercise — a stub keeps the test on the save flow itself.
const StubForm = ({ values, onChange }: { values: any; onChange: (patch: any) => void }) => (
  <input
    aria-label="stub-name"
    value={String(values.name ?? '')}
    onChange={(e) => onChange({ name: e.target.value })}
  />
);

vi.mock('../../../components/catalog/forms/catalogForms', () => ({
  getCatalogForm: () => StubForm,
}));

vi.mock('../../../components/biomarkers/BiomarkerMigrationWatcher', () => ({
  BiomarkerMigrationWatcher: (): ReactElement => <div />,
}));

vi.mock('../../../store/slices/authSlice', () => ({
  useAuthStore: (selector: (s: unknown) => unknown) =>
    selector({ user: { id: 'user-1', role: 'SYSTEM_ADMIN' } }),
}));

vi.mock('../../../store/slices/uiSlice', () => {
  const state = {
    pageHeaderConfig: null,
    setPageHeaderConfig: vi.fn(),
    pageSearchTerm: '',
    setPageSearchTerm: vi.fn(),
    setIsPageSearchSupported: vi.fn(),
    showConfirmation: (opts: { onConfirm?: () => void }) => opts.onConfirm?.(),
  };
  const useUIStore: any = (selector: (s: unknown) => unknown) => selector(state);
  useUIStore.getState = () => state;
  return { useUIStore };
});

import { CatalogWorkspace } from '../CatalogWorkspace';
import { toast } from 'react-toastify';
import {
  createCatalogItem,
  updateCatalogItem,
  deleteCatalogItem,
  listCatalogTypes,
  listCatalogItems,
} from '../../../services/catalogService';
import {
  createConcept,
  updateConcept,
  deleteConcept,
  restoreConcept,
} from '../../../services/conceptService';
import { anatomyService } from '../../../services/anatomyService';

function metaFor(type: CatalogTypeMeta['type'], write: CatalogTypeMeta['write']): CatalogTypeMeta {
  return {
    type,
    ui: { label_key: 'x', icon: 'Activity', color: 'blue', admin_route: '/x' },
    has_concept_link: true,
    edge_endpoint_type: type === 'vaccine' ? 'immunization' : type,
    search_columns: [],
    write,
  };
}

const MEDICATION_META = metaFor('medication', {
  create_fields: ['name', 'description', 'side_effects', 'dosage_info'],
  update_fields: ['name', 'description', 'side_effects', 'dosage_info'],
  supports_promote: true,
});

const CONCEPT_META = metaFor('concept', {
  create_fields: ['name', 'slug', 'kinds', 'tenant_scoped', 'description'],
  update_fields: ['name', 'kinds', 'description', 'status', 'meta_data'],
  supports_promote: false,
});

const ANATOMY_META = metaFor('anatomy', {
  create_fields: ['name', 'slug', 'class_concept_id', 'standard_system', 'standard_code', 'description'],
  update_fields: ['name', 'slug', 'class_concept_id', 'standard_system', 'standard_code', 'description'],
  supports_promote: true,
});

const conceptRow = {
  id: 'c-1',
  name: 'Old Concept',
  slug: 'old-concept',
  kinds: ['disease'],
  status: 'active',
  description: null,
  meta_data: {},
  seed_key: 'concept:old-concept',
  scope: 'system',
  tenant_id: null,
  created_by: null,
};

describe('<CatalogWorkspace> write path', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listCatalogItems).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(createCatalogItem).mockResolvedValue({ id: 'new-1', name: 'x' });
    vi.mocked(updateCatalogItem).mockImplementation(async (_t, _id, payload) => ({
      id: 'c-1',
      name: (payload as Record<string, unknown>).name,
    }));
  });

  it('creates a medication through the generic endpoint with the filtered payload', async () => {
    vi.mocked(listCatalogTypes).mockResolvedValue({ types: [MEDICATION_META] });
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/?type=medication']}>
        <CatalogWorkspace />
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole('button', { name: 'New' }));
    await user.type(await screen.findByLabelText('stub-name'), 'Aspirin');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(createCatalogItem).toHaveBeenCalledTimes(1));
    expect(createCatalogItem).toHaveBeenCalledWith('medication', { name: 'Aspirin' });
    expect(createConcept).not.toHaveBeenCalled();
    expect(anatomyService.create).not.toHaveBeenCalled();
  });

  it('surfaces a 422 array detail as a readable first message on save failure', async () => {
    // The workspace error toast goes through the same apiErrorDetail contract
    // as the dialog: a FastAPI 422 array `detail` renders as `field: msg`
    // (not `[object Object]` / not swallowed).
    vi.mocked(listCatalogTypes).mockResolvedValue({ types: [MEDICATION_META] });
    vi.mocked(createCatalogItem).mockRejectedValue({
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
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/?type=medication']}>
        <CatalogWorkspace />
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole('button', { name: 'New' }));
    await user.type(await screen.findByLabelText('stub-name'), 'Aspirin');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('name: Field required'),
    );
  });

  it('updates a concept through the generic endpoint (no per-type concept write)', async () => {
    vi.mocked(listCatalogTypes).mockResolvedValue({ types: [CONCEPT_META] });
    vi.mocked(listCatalogItems).mockResolvedValue({ items: [conceptRow as any], total: 1 });
    vi.mocked(updateCatalogItem).mockResolvedValue({ ...conceptRow, name: 'Renamed Concept' });
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/?type=concept&item=c-1']}>
        <CatalogWorkspace />
      </MemoryRouter>,
    );

    await user.click(await screen.findByTitle('Edit'));
    const input = await screen.findByLabelText('stub-name');
    await user.clear(input);
    await user.type(input, 'Renamed Concept');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(updateCatalogItem).toHaveBeenCalledTimes(1));
    expect(updateCatalogItem).toHaveBeenCalledWith('concept', 'c-1', {
      name: 'Renamed Concept',
    });
    expect(updateConcept).not.toHaveBeenCalled();
  });

  it('creates anatomy through the generic endpoint (no per-type anatomy write)', async () => {
    vi.mocked(listCatalogTypes).mockResolvedValue({ types: [ANATOMY_META] });
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/?type=anatomy']}>
        <CatalogWorkspace />
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole('button', { name: 'New' }));
    await user.type(await screen.findByLabelText('stub-name'), 'Femur');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(createCatalogItem).toHaveBeenCalledTimes(1));
    expect(createCatalogItem).toHaveBeenCalledWith('anatomy', { name: 'Femur' });
    expect(anatomyService.create).not.toHaveBeenCalled();
    expect(createConcept).not.toHaveBeenCalled();
  });

  it('deletes any type through the generic endpoint (concept retire included)', async () => {
    vi.mocked(listCatalogTypes).mockResolvedValue({ types: [CONCEPT_META] });
    vi.mocked(listCatalogItems).mockResolvedValue({ items: [conceptRow as any], total: 1 });
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/?type=concept&item=c-1']}>
        <CatalogWorkspace />
      </MemoryRouter>,
    );

    await user.click(await screen.findByTitle('Delete'));
    await waitFor(() => expect(deleteCatalogItem).toHaveBeenCalledTimes(1));
    expect(deleteCatalogItem).toHaveBeenCalledWith('concept', 'c-1');
    expect(deleteConcept).not.toHaveBeenCalled();
  });

  it('shows Restore only for retired concepts and calls restoreConcept', async () => {
    vi.mocked(listCatalogTypes).mockResolvedValue({ types: [CONCEPT_META] });
    vi.mocked(listCatalogItems).mockResolvedValue({
      items: [{ ...conceptRow, status: 'retired' } as any],
      total: 1,
    });
    vi.mocked(restoreConcept).mockResolvedValue({ ...conceptRow, status: 'active' } as any);
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/?type=concept&item=c-1']}>
        <CatalogWorkspace />
      </MemoryRouter>,
    );

    await user.click(await screen.findByTitle('Restore'));
    await waitFor(() => expect(restoreConcept).toHaveBeenCalledWith('c-1'));
  });

  it('hides Restore for other catalog types even when status is retired', async () => {
    vi.mocked(listCatalogTypes).mockResolvedValue({ types: [MEDICATION_META] });
    vi.mocked(listCatalogItems).mockResolvedValue({
      items: [{ id: 'm-1', name: 'Aspirin', status: 'retired' } as any],
      total: 1,
    });
    render(
      <MemoryRouter initialEntries={['/?type=medication&item=m-1']}>
        <CatalogWorkspace />
      </MemoryRouter>,
    );

    await screen.findByTitle('Edit');
    expect(screen.queryByTitle('Restore')).not.toBeInTheDocument();
  });
});
