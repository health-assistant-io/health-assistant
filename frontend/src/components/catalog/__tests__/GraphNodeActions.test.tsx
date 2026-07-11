import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const mockNavigate = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

import { GraphNodeActions } from '../GraphNodeActions';

const renderActions = (props: React.ComponentProps<typeof GraphNodeActions>) =>
  render(
    <MemoryRouter>
      <GraphNodeActions {...props} />
    </MemoryRouter>,
  );

describe('GraphNodeActions (toolbar variant)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the Catalog button for every type', () => {
    renderActions({ type: 'biomarker', id: 'b1' });
    expect(screen.getByTitle('Open in catalog')).toBeInTheDocument();
  });

  it('navigates to the catalog workspace with type + item on click', () => {
    renderActions({ type: 'medication', id: 'm9' });
    fireEvent.click(screen.getByTitle('Open in catalog'));
    expect(mockNavigate).toHaveBeenCalledWith(
      '/catalogs?type=medication&item=m9',
    );
  });

  it('renders the Domain link only for types with a domain page', () => {
    const { rerender } = render(
      <MemoryRouter>
        <GraphNodeActions type="biomarker" id="b1" />
      </MemoryRouter>,
    );
    expect(screen.getByTitle('Open in domain')).toBeInTheDocument();

    rerender(
      <MemoryRouter>
        <GraphNodeActions type="concept" id="c1" />
      </MemoryRouter>,
    );
    expect(screen.queryByTitle('Open in domain')).not.toBeInTheDocument();
  });

  it('renders the domain link as an anchor with the correct href', () => {
    renderActions({ type: 'biomarker', id: 'b1' });
    expect(screen.getByTitle('Open in domain')).toHaveAttribute(
      'href',
      '/biomarkers/details/b1',
    );
  });

  it('uses the slug for anatomy domain links', () => {
    renderActions({ type: 'anatomy', id: 'id-1', slug: 'thyroid' });
    expect(screen.getByTitle('Open in domain')).toHaveAttribute(
      'href',
      '/anatomy/thyroid',
    );
  });

  it('renders a Focus button when onFocus is provided and calls it', () => {
    const onFocus = vi.fn();
    renderActions({ type: 'biomarker', id: 'b1', onFocus });
    fireEvent.click(screen.getByTitle('Focus'));
    expect(onFocus).toHaveBeenCalledTimes(1);
  });

  it('does not render Focus when onFocus is omitted', () => {
    renderActions({ type: 'biomarker', id: 'b1' });
    expect(screen.queryByTitle('Focus')).not.toBeInTheDocument();
  });
});

describe('GraphNodeActions (menu variant)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders full labels in the menu variant', () => {
    renderActions({ type: 'biomarker', id: 'b1', variant: 'menu' });
    expect(screen.getByText('Open in catalog')).toBeInTheDocument();
    expect(screen.getByText('Open in domain')).toBeInTheDocument();
  });

  it('navigates on catalog click in menu variant', () => {
    renderActions({ type: 'medication', id: 'm9', variant: 'menu' });
    fireEvent.click(screen.getByText('Open in catalog'));
    expect(mockNavigate).toHaveBeenCalledWith(
      '/catalogs?type=medication&item=m9',
    );
  });
});
