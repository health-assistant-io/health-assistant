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
    t: (k: string, dftOrOpts?: unknown) => {
      if (typeof dftOrOpts === 'string') return dftOrOpts;
      let s = (dftOrOpts as { defaultValue?: string })?.defaultValue ?? k;
      const opts = (dftOrOpts ?? {}) as Record<string, unknown>;
      for (const [key, val] of Object.entries(opts)) {
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
vi.mock('../../../services/catalogService', () => ({
  searchCatalogs: (...args: unknown[]) => searchCatalogsMock(...args),
  listCatalogItems: (...args: unknown[]) => listCatalogItemsMock(...args),
  listCatalogTypes: (...args: unknown[]) => listCatalogTypesMock(...args),
  getRelationType: vi.fn(),
  getRelationTypes: vi.fn(async () => ({ items: [] })),
  loadRelationTypes: vi.fn(async () => undefined),
  getCatalogItem: vi.fn(),
  getCatalogGraph: vi.fn(async () => ({ nodes: [], edges: [] })),
}));

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
