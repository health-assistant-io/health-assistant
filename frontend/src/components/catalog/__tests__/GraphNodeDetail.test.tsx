import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

const mockGetCatalogItem = vi.fn();
vi.mock('../../../services/catalogService', () => ({
  getCatalogItem: (...args: unknown[]) => mockGetCatalogItem(...args),
}));

import { GraphNodeDetail, __resetDetailCache } from '../GraphNodeDetail';

const baseNode = {
  id: 'b1',
  name: 'Hemoglobin A1c',
  type: 'biomarker',
  primary_kind: 'biomarker_class',
  color: '#06b6d4',
};

describe('GraphNodeDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetCatalogItem.mockReset();
    __resetDetailCache();
  });

  it('renders the node name, type label, and degree', () => {
    mockGetCatalogItem.mockResolvedValue({});
    render(
      <MemoryRouter>
        <GraphNodeDetail node={baseNode} degree={5} />
      </MemoryRouter>,
    );
    expect(screen.getByText('Hemoglobin A1c')).toBeInTheDocument();
    expect(screen.getByText(/Biomarkers/i)).toBeInTheDocument();
    expect(screen.getByText(/5 relations/i)).toBeInTheDocument();
  });

  it('lazy-fetches the item detail and shows the description', async () => {
    mockGetCatalogItem.mockResolvedValue({
      description: 'Average blood glucose over 3 months.',
      code: 'HBA1C',
    });
    render(
      <MemoryRouter>
        <GraphNodeDetail node={baseNode} degree={1} />
      </MemoryRouter>,
    );
    expect(mockGetCatalogItem).toHaveBeenCalledWith('biomarker', 'b1');
    await waitFor(() => {
      expect(
        screen.getByText(/average blood glucose/i),
      ).toBeInTheDocument();
      expect(screen.getByText(/HBA1C/)).toBeInTheDocument();
    });
  });

  it('caches the fetch so a re-render does not refetch', async () => {
    mockGetCatalogItem.mockResolvedValue({ description: 'cached' });
    const { rerender } = render(
      <MemoryRouter>
        <GraphNodeDetail node={baseNode} degree={3} />
      </MemoryRouter>,
    );
    await waitFor(() => expect(mockGetCatalogItem).toHaveBeenCalledTimes(1));
    // Re-render the same node — should not refetch.
    rerender(
      <MemoryRouter>
        <GraphNodeDetail node={baseNode} degree={3} />
      </MemoryRouter>,
    );
    await waitFor(() => expect(mockGetCatalogItem).toHaveBeenCalledTimes(1));
  });

  it('renders the Catalog action button', () => {
    mockGetCatalogItem.mockResolvedValue({});
    render(
      <MemoryRouter>
        <GraphNodeDetail node={baseNode} degree={2} />
      </MemoryRouter>,
    );
    expect(screen.getByTitle('Open in catalog')).toBeInTheDocument();
  });

  it('renders the Domain link for biomarker', () => {
    mockGetCatalogItem.mockResolvedValue({});
    render(
      <MemoryRouter>
        <GraphNodeDetail node={baseNode} degree={2} />
      </MemoryRouter>,
    );
    expect(screen.getByTitle('Open in domain')).toHaveAttribute(
      'href',
      '/biomarkers/details/b1',
    );
  });

  it('does not render the Domain link for concept type', () => {
    mockGetCatalogItem.mockResolvedValue({});
    render(
      <MemoryRouter>
        <GraphNodeDetail
          node={{ ...baseNode, type: 'concept' }}
          degree={2}
        />
      </MemoryRouter>,
    );
    expect(screen.queryByTitle('Open in domain')).not.toBeInTheDocument();
  });

  it('calls onClose when the close button is clicked', () => {
    mockGetCatalogItem.mockResolvedValue({});
    const onClose = vi.fn();
    render(
      <MemoryRouter>
        <GraphNodeDetail node={baseNode} degree={2} onClose={onClose} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByTitle('Close'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('uses singular "relation" when degree is 1', () => {
    mockGetCatalogItem.mockResolvedValue({});
    render(
      <MemoryRouter>
        <GraphNodeDetail node={baseNode} degree={1} />
      </MemoryRouter>,
    );
    expect(screen.getByText(/1 relation/i)).toBeInTheDocument();
  });

  it('passes the lazily-fetched slug to the domain link for anatomy', async () => {
    mockGetCatalogItem.mockResolvedValue({ slug: 'thyroid' });
    render(
      <MemoryRouter>
        <GraphNodeDetail
          node={{ ...baseNode, type: 'anatomy', id: 'a1' }}
          degree={2}
        />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByTitle('Open in domain')).toHaveAttribute(
        'href',
        '/anatomy/thyroid',
      );
    });
  });
});
