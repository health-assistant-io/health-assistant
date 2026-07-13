import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, opts?: any) => {
      let s = opts?.defaultValue ?? k;
      if (opts) {
        for (const [key, val] of Object.entries(opts)) {
          if (key === 'defaultValue') continue;
          s = s.replace(new RegExp(`{{\\s*${key}\\s*}}`, 'g'), String(val));
        }
      }
      return s;
    },
  }),
}));

import { FilterBar } from '../FilterBar';
import { useFilterState } from '../useFilterState';
import type { FacetDefinition } from '../types';

interface Item {
  category: string;
  telemetry: boolean;
}

const categoryFacet: FacetDefinition<Item> = {
  id: 'category',
  label: 'Category',
  kind: 'multi',
  mode: 'client',
  getOptions: (items) => {
    const counts = new Map<string, number>();
    for (const i of items) counts.set(i.category, (counts.get(i.category) ?? 0) + 1);
    return [...counts.entries()].map(([value, count]) => ({ value, label: value, count }));
  },
  predicate: (item, value) => value.kind === 'multi' && value.values.includes(item.category),
};

const telemetryFacet: FacetDefinition<Item> = {
  id: 'telemetry',
  label: 'Telemetry',
  kind: 'toggle',
  mode: 'client',
  predicate: (item, value) => value.kind !== 'toggle' || !value.on || item.telemetry,
};

const hiddenFacet: FacetDefinition<Item> = {
  id: 'extra',
  label: 'Extra',
  kind: 'toggle',
  mode: 'client',
  defaultHidden: true,
  predicate: () => true,
};

const items: Item[] = [
  { category: 'Lipids', telemetry: true },
  { category: 'Lipids', telemetry: false },
  { category: 'Glucose', telemetry: true },
];

function setup(facets: FacetDefinition<Item>[]) {
  function Harness() {
    const filter = useFilterState(facets);
    return (
      <FilterBar<Item>
        facets={facets}
        filter={filter}
        items={items}
        resultCount={filter.applyFilters(items).length}
        totalCount={items.length}
      />
    );
  }
  return render(<Harness />);
}

describe('FilterBar', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders one chip per visible facet', () => {
    setup([categoryFacet, telemetryFacet]);
    expect(screen.getByText('Category')).toBeInTheDocument();
    expect(screen.getByText('Telemetry')).toBeInTheDocument();
  });

  it('does not show "Clear all" when nothing is active', () => {
    setup([categoryFacet, telemetryFacet]);
    expect(screen.queryByText('Clear all')).not.toBeInTheDocument();
  });

  it('shows result count text', () => {
    setup([categoryFacet]);
    expect(screen.getByText('3 of 3')).toBeInTheDocument();
  });

  it('hides defaultHidden facets behind "More"', () => {
    setup([categoryFacet, hiddenFacet]);
    expect(screen.queryByText('Extra')).not.toBeInTheDocument();
    expect(screen.getByText('More')).toBeInTheDocument();
  });

  it('reveals hidden facets when "More" is clicked', () => {
    setup([categoryFacet, hiddenFacet]);
    fireEvent.click(screen.getByText('More'));
    expect(screen.getByText('Extra')).toBeInTheDocument();
    expect(screen.getByText('Fewer')).toBeInTheDocument();
  });

  it('does not render the More button when there are no hidden facets', () => {
    setup([categoryFacet, telemetryFacet]);
    expect(screen.queryByText('More')).not.toBeInTheDocument();
  });
});

describe('FilterBar interaction (wired to useFilterState)', () => {
  it('activating a facet shows "Clear all"', () => {
    function Harness() {
      const filter = useFilterState([telemetryFacet]);
      return (
        <FilterBar<Item>
          facets={[telemetryFacet]}
          filter={filter}
          items={items}
        />
      );
    }
    render(<Harness />);
    expect(screen.queryByText('Clear all')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Telemetry'));
    expect(screen.getByText('Clear all')).toBeInTheDocument();
  });

  it('Clear all resets every facet', () => {
    function Harness() {
      const filter = useFilterState([telemetryFacet]);
      return (
        <FilterBar<Item>
          facets={[telemetryFacet]}
          filter={filter}
          items={items}
        />
      );
    }
    render(<Harness />);
    fireEvent.click(screen.getByText('Telemetry'));
    expect(screen.getByText('Clear all')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Clear all'));
    expect(screen.queryByText('Clear all')).not.toBeInTheDocument();
  });
});
