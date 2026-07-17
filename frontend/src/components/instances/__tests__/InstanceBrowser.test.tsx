import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, dftOrOpts?: any) => {
      let s: string;
      let opts: Record<string, unknown>;
      if (typeof dftOrOpts === 'string') {
        s = dftOrOpts;
        opts = {};
      } else {
        s = dftOrOpts?.defaultValue ?? k;
        opts = dftOrOpts ?? {};
      }
      for (const [key, val] of Object.entries(opts)) {
        if (key === 'defaultValue') continue;
        s = s.replace(new RegExp(`{{\\s*${key}\\s*}}`, 'g'), String(val));
      }
      return s;
    },
    i18n: { language: 'en' },
  }),
}));

import { InstanceBrowser } from '../InstanceBrowser';
import type { InstanceRow } from '../types';

function makeRow(over: Partial<InstanceRow> = {}): InstanceRow {
  return {
    id: over.id ?? 'r1',
    type: over.type ?? 'examination',
    label: over.label ?? 'Blood Test',
    subtitle: over.subtitle,
    date: over.date,
    status: over.status,
    statusColor: over.statusColor,
    icon: over.icon,
    badges: over.badges,
    raw: over.raw ?? {},
  };
}

const rows: InstanceRow[] = [
  makeRow({ id: 'a', label: 'Zebra Exam', date: '2026-01-01T00:00:00Z' }),
  makeRow({ id: 'b', label: 'Alpha Exam', date: '2026-03-01T00:00:00Z' }),
  makeRow({ id: 'c', label: 'Mid Exam', date: '2026-02-01T00:00:00Z' }),
];

describe('InstanceBrowser', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders all rows', () => {
    render(
      <InstanceBrowser rows={rows} loading={false} total={rows.length} />,
    );
    expect(screen.getByText('Zebra Exam')).toBeInTheDocument();
    expect(screen.getByText('Alpha Exam')).toBeInTheDocument();
    expect(screen.getByText('Mid Exam')).toBeInTheDocument();
  });

  it('sorts by name (asc) when the Name sort button is clicked', () => {
    const { container } = render(
      <InstanceBrowser rows={rows} loading={false} total={rows.length} />,
    );
    // Default sort is date desc. Switch to Name asc.
    fireEvent.click(screen.getByText('Name'));
    const labels = Array.from(container.querySelectorAll('[data-row-id]')).map(
      (li) => li.getAttribute('data-row-id'),
    );
    // Alpha, Mid, Zebra (asc)
    expect(labels).toEqual(['b', 'c', 'a']);
  });

  it('shows the empty (no records) state when there are no rows and no filters', () => {
    render(<InstanceBrowser rows={[]} loading={false} total={0} />);
    expect(screen.getByText('No records yet')).toBeInTheDocument();
  });

  it('shows the no-matches state with a Clear filters button when filters are active', () => {
    const onClear = vi.fn();
    render(
      <InstanceBrowser
        rows={[]}
        loading={false}
        total={0}
        hasActiveFilters
        onClearFilters={onClear}
      />,
    );
    const clearBtn = screen.getByText('Clear filters');
    fireEvent.click(clearBtn);
    expect(onClear).toHaveBeenCalledOnce();
  });

  it('renders Add/Added toggle in picker mode and calls onTogglePick', () => {
    const onToggle = vi.fn();
    render(
      <InstanceBrowser
        rows={rows}
        loading={false}
        total={rows.length}
        pickedIds={['a']}
        onTogglePick={onToggle}
      />,
    );
    // 'a' is picked -> shows "Added"; others show "Add".
    const addButtons = screen.getAllByText('Add');
    expect(addButtons).toHaveLength(2);
    expect(screen.getByText('Added')).toBeInTheDocument();
    fireEvent.click(addButtons[0]);
    expect(onToggle).toHaveBeenCalledOnce();
  });

  it('renders Load more when hasMore and calls onLoadMore', () => {
    const onLoadMore = vi.fn();
    render(
      <InstanceBrowser
        rows={rows}
        loading={false}
        total={10}
        hasMore
        onLoadMore={onLoadMore}
      />,
    );
    const btn = screen.getByText('Load more');
    fireEvent.click(btn);
    expect(onLoadMore).toHaveBeenCalledOnce();
  });

  it('renders the type chip in multi-type (showTypeSort) mode', () => {
    render(
      <InstanceBrowser
        rows={[
          makeRow({ id: 'x', type: 'medication', label: 'Aspirin' }),
        ]}
        loading={false}
        total={1}
        showTypeSort
      />,
    );
    expect(screen.getByText('medication')).toBeInTheDocument();
  });

  it('shows a loading skeleton when loading', () => {
    const { container } = render(
      <InstanceBrowser rows={[]} loading total={0} />,
    );
    // Skeleton renders list items with animate-pulse.
    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0);
  });
});
