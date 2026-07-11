import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, opts?: any) => opts?.defaultValue ?? k }),
}));

vi.mock('../../../services/catalogService', () => {
  const RELATION_TYPES = [
    { value: 'TREATS', label: 'Treats', group: 'Medical knowledge', description: '', icon: { type: 'lucide', value: 'Syringe' } },
    { value: 'MEMBER_OF', label: 'Member of', group: 'Structural', description: '', icon: { type: 'lucide', value: 'Link2' } },
  ];
  return {
    loadRelationTypes: vi.fn().mockResolvedValue(RELATION_TYPES),
    getRelationTypes: vi.fn(() => RELATION_TYPES),
  };
});

import { GraphRelationFilter } from '../GraphRelationFilter';

const edges = [
  { id: 'e1', source: 'a', target: 'b', relation: 'TREATS' },
  { id: 'e2', source: 'a', target: 'c', relation: 'TREATS' },
  { id: 'e3', source: 'd', target: 'e', relation: 'MEMBER_OF' },
];

describe('GraphRelationFilter', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders a chip for each relation type present in the edges', () => {
    render(
      <GraphRelationFilter
        edges={edges}
        hidden={new Set()}
        onToggle={() => {}}
      />,
    );
    expect(screen.getByText(/Treats/)).toBeInTheDocument();
    expect(screen.getByText(/Member of/)).toBeInTheDocument();
  });

  it('shows the edge count per relation type', () => {
    render(
      <GraphRelationFilter
        edges={edges}
        hidden={new Set()}
        onToggle={() => {}}
      />,
    );
    // TREATS has 2 edges, MEMBER_OF has 1.
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();
  });

  it('calls onToggle with the relation value when a chip is clicked', () => {
    const onToggle = vi.fn();
    render(
      <GraphRelationFilter
        edges={edges}
        hidden={new Set()}
        onToggle={onToggle}
      />,
    );
    fireEvent.click(screen.getByText(/Treats/));
    expect(onToggle).toHaveBeenCalledWith('TREATS');
  });

  it('renders hidden relations with dimmed styling', () => {
    render(
      <GraphRelationFilter
        edges={edges}
        hidden={new Set(['TREATS'])}
        onToggle={() => {}}
      />,
    );
    const treatsChip = screen.getByText(/Treats/).closest('button');
    expect(treatsChip?.className).toMatch(/opacity|line-through/);
  });

  it('does not render relation types absent from the edges', () => {
    render(
      <GraphRelationFilter
        edges={[{ id: 'e1', source: 'a', target: 'b', relation: 'TREATS' }]}
        hidden={new Set()}
        onToggle={() => {}}
      />,
    );
    expect(screen.getByText(/Treats/)).toBeInTheDocument();
    expect(screen.queryByText(/Member of/)).not.toBeInTheDocument();
  });
});
