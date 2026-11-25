import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

const mockNavigate = vi.fn();

import { GraphNodeContextMenu } from '../GraphNodeContextMenu';

const props = (overrides: Partial<React.ComponentProps<typeof GraphNodeContextMenu>> = {}) => ({
  x: 100,
  y: 200,
  type: 'biomarker',
  id: 'b1',
  onClose: vi.fn(),
  onFocus: vi.fn(),
  ...overrides,
});

describe('GraphNodeContextMenu', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the menu actions (catalog + domain for biomarker)', () => {
    render(
      <MemoryRouter>
        <GraphNodeContextMenu {...props()} />
      </MemoryRouter>,
    );
    expect(screen.getByText('catalogs.open_in_catalog')).toBeInTheDocument();
    expect(screen.getByText('catalogs.open_in_domain')).toBeInTheDocument();
  });

  it('hides domain action for types without a domain page', () => {
    render(
      <MemoryRouter>
        <GraphNodeContextMenu {...props({ type: 'concept' })} />
      </MemoryRouter>,
    );
    expect(screen.getByText('catalogs.open_in_catalog')).toBeInTheDocument();
    expect(screen.queryByText('catalogs.open_in_domain')).not.toBeInTheDocument();
  });

  it('closes on Escape', () => {
    const onClose = vi.fn();
    render(
      <MemoryRouter>
        <GraphNodeContextMenu {...props({ onClose })} />
      </MemoryRouter>,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes on outside mousedown', () => {
    const onClose = vi.fn();
    render(
      <MemoryRouter>
        <GraphNodeContextMenu {...props({ onClose })} />
      </MemoryRouter>,
    );
    // Simulate a mousedown outside the menu (on document body).
    fireEvent.mouseDown(document.body);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does not close on mousedown inside the menu', () => {
    const onClose = vi.fn();
    render(
      <MemoryRouter>
        <GraphNodeContextMenu {...props({ onClose })} />
      </MemoryRouter>,
    );
    const menu = screen.getByText('catalogs.open_in_catalog').closest('.context-menu-root');
    fireEvent.mouseDown(menu!);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('navigates to catalog when the catalog action is clicked', () => {
    render(
      <MemoryRouter>
        <GraphNodeContextMenu {...props({ type: 'medication', id: 'm9' })} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText('catalogs.open_in_catalog'));
    expect(mockNavigate).toHaveBeenCalledWith('/catalogs?type=medication&item=m9');
  });
});
