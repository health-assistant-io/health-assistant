/**
 * CatalogItemPicker tests (the multi-select-picker adapter).
 *
 * Covers:
 *   1. Type-ahead → add → onChange maps back to CatalogSelection.
 *   2. Chips: type badge without relation mode, relation dropdown with it;
 *      per-chip remove reports back through onChange.
 *   3. Browse modal: the shared PickerBrowseModal core (search / rows /
 *      Add-Added / count footer) driven by the catalog data closures.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useState, type ComponentProps } from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

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

const searchCatalogsMock = vi.fn();
const listCatalogItemsMock = vi.fn();
const listCatalogTypesMock = vi.fn();
const createCatalogItemMock = vi.fn();
vi.mock('../../../services/catalogService', () => ({
  searchCatalogs: (...args: unknown[]) => searchCatalogsMock(...args),
  listCatalogItems: (...args: unknown[]) => listCatalogItemsMock(...args),
  listCatalogTypes: (...args: unknown[]) => listCatalogTypesMock(...args),
  createCatalogItem: (...args: unknown[]) => createCatalogItemMock(...args),
  getRelationType: vi.fn(),
  getRelationTypes: vi.fn(async () => ({ items: [] })),
  loadRelationTypes: vi.fn(async () => undefined),
  getCatalogItem: vi.fn(),
  getCatalogGraph: vi.fn(async () => ({ nodes: [], edges: [] })),
}));

vi.mock('../../../services/biomarkerService', () => ({
  __esModule: true,
  default: {
    getUnits: vi.fn().mockResolvedValue([]),
    getStates: vi.fn().mockResolvedValue([]),
    syncReferenceRanges: vi.fn().mockResolvedValue(undefined),
  },
}));

vi.mock('../../../services/conceptEdges', () => ({
  getLinkSchema: vi.fn().mockResolvedValue([]),
  createLinksFor: vi.fn().mockResolvedValue([]),
  selectionsToLinkInputs: vi.fn(() => []),
}));

vi.mock('react-toastify', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warn: vi.fn() },
}));

vi.mock('../../ui/AIAssistButton', () => ({ AIAssistButton: () => null }));
vi.mock('../../ui/RichTextEditor', () => ({ RichTextEditor: () => <div /> }));

vi.mock('../../../store/slices/authSlice', () => ({
  useAuthStore: (sel: (s: { user: { id: string } | null }) => unknown) =>
    sel({ user: { id: 'u1' } }),
}));

vi.mock('../CatalogPickerGraph', () => ({
  CatalogPickerGraph: ({
    onTogglePick,
  }: {
    onTogglePick: (item: unknown, type: string) => void;
  }) => (
    <button
      type="button"
      onClick={() => onTogglePick({ id: 'm2', name: 'Ibuprofen' }, 'medication')}
    >
      pick graph node
    </button>
  ),
}));

import { CatalogItemPicker } from '../CatalogItemPicker';
import type { CatalogSelection } from '../../../types/catalog';

const medicationItem = {
  id: 'm1',
  name: 'Aspirin',
  description: 'Salicylate analgesic',
  scope: 'system',
  updated_at: '2026-01-01T00:00:00Z',
  relation_count: 2,
};
const secondItem = {
  id: 'm2',
  name: 'Ibuprofen',
  scope: 'tenant',
};

beforeEach(() => {
  vi.clearAllMocks();
  searchCatalogsMock.mockResolvedValue({
    results: [
      { type: 'medication', id: 'm1', label: 'Aspirin' },
      { type: 'biomarker', id: 'b9', label: 'CRP' },
    ],
  });
  listCatalogTypesMock.mockResolvedValue({
    types: [
      {
        type: 'medication',
        ui: { icon: 'Pill', color: '#123456', admin_route: '/meds' },
        has_concept_link: false,
        edge_endpoint_type: 'medication',
        search_columns: [],
      },
    ],
  });
  listCatalogItemsMock.mockResolvedValue({
    items: [medicationItem, secondItem],
    total: 2,
  });
  createCatalogItemMock.mockResolvedValue({
    id: 'm9',
    name: 'New Med',
    slug: 'new-med',
    seed_key: 'medication:new-med',
  });
});

const renderPicker = (props: Partial<ComponentProps<typeof CatalogItemPicker>> = {}) => {
  const onChange = vi.fn();
  const initial = props.value ?? [];
  const Harness = () => {
    const [value, setValue] = useState<CatalogSelection[]>(initial);
    return (
      <CatalogItemPicker
        allowedTypes={['medication']}
        {...props}
        value={value}
        onChange={(next) => {
          setValue(next);
          onChange(next);
        }}
      />
    );
  };
  render(<Harness />);
  return onChange;
};

describe('CatalogItemPicker type-ahead', () => {
  it('adds a pick through the shared type-ahead and maps it to CatalogSelection', async () => {
    const onChange = renderPicker();
    const input = screen.getByRole('combobox', { name: 'Search any catalog…' });
    fireEvent.change(input, { target: { value: 'asp' } });
    const option = await screen.findByRole('option', { name: /Aspirin/ });
    fireEvent.click(option);
    expect(onChange).toHaveBeenCalledWith([
      { type: 'medication', id: 'm1', label: 'Aspirin' },
    ]);
  });

  it('stamps the default relation on new picks in relation mode', async () => {
    const onChange = renderPicker({ relationPicker: {} });
    fireEvent.change(screen.getByRole('combobox', { name: 'Search any catalog…' }), {
      target: { value: 'asp' },
    });
    fireEvent.click(await screen.findByRole('option', { name: /Aspirin/ }));
    expect(onChange).toHaveBeenCalledWith([
      { type: 'medication', id: 'm1', label: 'Aspirin', relation: 'AFFECTS' },
    ]);
  });
});

describe('CatalogItemPicker chips', () => {
  it('renders the type badge and a remove per chip', async () => {
    const onChange = renderPicker({
      value: [{ type: 'medication', id: 'm1', label: 'Aspirin' }],
    });
    expect(screen.getByText('medication')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Remove Aspirin' }));
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it('renders the relation dropdown on chips in relation mode', () => {
    renderPicker({
      relationPicker: {},
      value: [{ type: 'medication', id: 'm1', label: 'Aspirin', relation: 'TREATS' }],
    });
    expect(screen.getByText('TREATS')).toBeInTheDocument();
    expect(screen.queryByText('medication')).not.toBeInTheDocument();
  });
});

describe('CatalogItemPicker browse modal', () => {
  it('lists catalog rows with Add/Added toggles and the live count', async () => {
    const onChange = renderPicker({
      value: [{ type: 'medication', id: 'm1', label: 'Aspirin' }],
    });
    fireEvent.click(screen.getByRole('button', { name: 'Browse' }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('Aspirin')).toBeInTheDocument();
    expect(listCatalogItemsMock).toHaveBeenCalledWith(
      'medication',
      expect.objectContaining({ limit: 50, offset: 0 }),
    );
    expect(within(dialog).getByRole('button', { name: 'Added Aspirin' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(within(dialog).getByText('1 selected')).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Add Ibuprofen' }));
    expect(onChange).toHaveBeenCalledWith([
      { type: 'medication', id: 'm1', label: 'Aspirin' },
      { type: 'medication', id: 'm2', label: 'Ibuprofen' },
    ]);
    await waitFor(() =>
      expect(within(dialog).getByText('2 selected')).toBeInTheDocument(),
    );
  });

  it('keeps the shared search + Add/Added core wired to the catalog closures', async () => {
    renderPicker();
    fireEvent.click(screen.getByRole('button', { name: 'Browse' }));
    const dialog = await screen.findByRole('dialog');
    const search = within(dialog).getByRole('searchbox');
    fireEvent.change(search, { target: { value: 'aspirin' } });
    await waitFor(() =>
      expect(listCatalogItemsMock).toHaveBeenCalledWith(
        'medication',
        expect.objectContaining({ search: 'aspirin' }),
      ),
    );
  });

  it('re-runs the list search when the scope filter changes (reloadKey)', async () => {
    renderPicker();
    fireEvent.click(screen.getByRole('button', { name: 'Browse' }));
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findByText('Aspirin');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Mine' }));
    await waitFor(() =>
      expect(listCatalogItemsMock).toHaveBeenCalledWith(
        'medication',
        expect.objectContaining({ scope: 'mine' }),
      ),
    );
  });

  it('wires the graph surface through the body ctx toggle', async () => {
    const onChange = renderPicker();
    fireEvent.click(screen.getByRole('button', { name: 'Browse' }));
    await screen.findByRole('dialog');
    fireEvent.click(screen.getByTitle('Graph view'));
    fireEvent.click(await screen.findByRole('button', { name: 'pick graph node' }));
    expect(onChange).toHaveBeenCalledWith([
      { type: 'medication', id: 'm2', label: 'Ibuprofen' },
    ]);
  });
});

describe('CatalogItemPicker browse-create (allowCreate)', () => {
  it('renders no New action when allowCreate is off', async () => {
    renderPicker({ mode: 'single' });
    fireEvent.click(screen.getByRole('button', { name: 'Browse' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).queryByText('New Medication')).not.toBeInTheDocument();
  });

  it('renders no New action in multi mode even when allowCreate is on', async () => {
    renderPicker({ allowCreate: true });
    fireEvent.click(screen.getByRole('button', { name: 'Browse' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).queryByText('New Medication')).not.toBeInTheDocument();
  });

  it('renders no New action when the field allows more than one catalog type', async () => {
    // Create is only offered for a SINGLE-type field — a multi-type picker
    // has no one type to define, even in single mode with allowCreate.
    renderPicker({
      mode: 'single',
      allowCreate: true,
      allowedTypes: ['medication', 'allergy'],
    });
    fireEvent.click(screen.getByRole('button', { name: 'Browse' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).queryByText('New Medication')).not.toBeInTheDocument();
    expect(within(dialog).queryByText('New Allergen')).not.toBeInTheDocument();
  });

  it('renders New <type> in the list header when allowCreate + single + list', async () => {
    renderPicker({ mode: 'single', allowCreate: true });
    fireEvent.click(screen.getByRole('button', { name: 'Browse' }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('Aspirin')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'New Medication' })).toBeInTheDocument();
  });

  it('renders no New action in the graph view', async () => {
    renderPicker({ mode: 'single', allowCreate: true });
    fireEvent.click(screen.getByRole('button', { name: 'Browse' }));
    await screen.findByRole('dialog');
    fireEvent.click(screen.getByTitle('Graph view'));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).queryByRole('button', { name: 'New Medication' })).not.toBeInTheDocument();
  });

  it('seeds the create dialog name from the browse search and selects + closes after create', async () => {
    const onChange = renderPicker({ mode: 'single', allowCreate: true });
    fireEvent.click(screen.getByRole('button', { name: 'Browse' }));
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findByText('Aspirin');

    fireEvent.change(within(dialog).getByRole('searchbox'), {
      target: { value: 'new me' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'New Medication' }));

    // The browse search text is the draft's starting name.
    await waitFor(() =>
      expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(2),
    );
    const dialogs = document.querySelectorAll('[role="dialog"]');
    const nameInput = within(dialogs[dialogs.length - 1] as HTMLElement).getAllByRole(
      'textbox',
    )[0];
    expect(nameInput).toHaveValue('new me');

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    // The created row (server identity) becomes the single pick, and both
    // dialogs close.
    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith([
        { type: 'medication', id: 'm9', label: 'New Med' },
      ]),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    // No reload bump — the list page was fetched once on open.
    expect(listCatalogItemsMock).toHaveBeenCalledTimes(1);
  });

  it('keeps the browse modal and the selection when the create dialog is cancelled', async () => {
    const onChange = renderPicker({ mode: 'single', allowCreate: true });
    fireEvent.click(screen.getByRole('button', { name: 'Browse' }));
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findByText('Aspirin');

    fireEvent.click(within(dialog).getByRole('button', { name: 'New Medication' }));
    await waitFor(() =>
      expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(2),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() =>
      expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1),
    );

    // The browse modal is still open and the selection is untouched.
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    expect(within(document.querySelector('[role="dialog"]') as HTMLElement).getByText('Aspirin')).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
});
