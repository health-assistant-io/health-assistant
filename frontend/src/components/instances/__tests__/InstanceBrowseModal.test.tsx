import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

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

import { InstanceBrowseModal } from '../InstanceBrowseModal';
import {
  registerAdapter,
  _clearAdaptersForTests,
} from '../instanceRegistry';
import type { InstanceAdapter, InstanceRow } from '../types';

const ITEMS = [
  { id: 'e1', name: 'Blood Test', category: 'Lab' },
  { id: 'e2', name: 'X-Ray Chest', category: 'Imaging' },
];

function makeAdapter(over: Partial<InstanceAdapter<any>> = {}): InstanceAdapter<any> {
  return {
    type: 'examination',
    entityLabel: { singular: 'Examination', plural: 'Examinations' },
    icon: 'Stethoscope',
    fetch: over.fetch ?? (async () => ({ items: ITEMS, total: ITEMS.length })),
    fetchOne: over.fetchOne ?? (async () => ITEMS[0]),
    facets: [],
    toRow: (item: any) => ({
      id: item.id,
      type: 'examination',
      label: item.name,
      subtitle: item.category,
      raw: item,
    }),
    toSelection: (item: any) => ({
      type: 'examination',
      id: item.id,
      label: item.name,
    }),
    detailRoute: () => null,
    ...over,
  };
}

describe('InstanceBrowseModal', () => {
  beforeEach(() => {
    _clearAdaptersForTests();
  });
  afterEach(() => {
    _clearAdaptersForTests();
  });

  it('fetches via the adapter and renders rows when open with a patient', async () => {
    registerAdapter(makeAdapter());
    render(
      <InstanceBrowseModal
        isOpen
        onClose={() => {}}
        picked={[]}
        onTogglePick={() => {}}
        allowedTypes={['examination']}
        patientId="patient-1"
      />,
    );
    await waitFor(
      () => {
        expect(screen.getByText('Blood Test')).toBeInTheDocument();
        expect(screen.getByText('X-Ray Chest')).toBeInTheDocument();
      },
      { timeout: 3000 },
    );
  });

  it('calls onTogglePick with the projected row when Add is clicked', async () => {
    const onToggle = vi.fn();
    registerAdapter(makeAdapter());
    render(
      <InstanceBrowseModal
        isOpen
        onClose={() => {}}
        picked={[]}
        onTogglePick={onToggle}
        allowedTypes={['examination']}
        patientId="patient-1"
      />,
    );
    await waitFor(
      () => expect(screen.getByText('Blood Test')).toBeInTheDocument(),
      { timeout: 3000 },
    );
    const addButtons = screen.getAllByText('Add');
    fireEvent.click(addButtons[0]);
    expect(onToggle).toHaveBeenCalledOnce();
    const row: InstanceRow = onToggle.mock.calls[0][0];
    expect(row.id).toBe('e1');
    expect(row.type).toBe('examination');
  });

  it('renders the preview pane when a row is clicked and shows its description', async () => {
    registerAdapter(makeAdapter());
    render(
      <InstanceBrowseModal
        isOpen
        onClose={() => {}}
        picked={[]}
        onTogglePick={() => {}}
        allowedTypes={['examination']}
        patientId="patient-1"
      />,
    );
    await waitFor(
      () => expect(screen.getByText('Blood Test')).toBeInTheDocument(),
      { timeout: 3000 },
    );
    // Click the row label (InstanceBrowser row click selects it for preview).
    fireEvent.click(screen.getByText('Blood Test'));
    // The empty-state hint disappears once a row is selected.
    expect(screen.queryByText('Select a record to preview')).not.toBeInTheDocument();
    // The preview header shows the type chip + label.
    expect(screen.getAllByText('Blood Test').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('examination').length).toBeGreaterThanOrEqual(1);
  });

  it('shows the "select a patient" guard when no patientId and adapter disallows tenant scope', () => {
    registerAdapter(makeAdapter()); // allowTenantScope undefined -> false
    render(
      <InstanceBrowseModal
        isOpen
        onClose={() => {}}
        picked={[]}
        onTogglePick={() => {}}
        allowedTypes={['examination']}
      />,
    );
    expect(screen.getByText('Select a patient')).toBeInTheDocument();
  });

  it('fetches tenant-wide when no patientId but adapter opts into tenant scope', async () => {
    const fetch = vi.fn(async () => ({ items: ITEMS, total: ITEMS.length }));
    registerAdapter(makeAdapter({ fetch, allowTenantScope: true }));
    render(
      <InstanceBrowseModal
        isOpen
        onClose={() => {}}
        picked={[]}
        onTogglePick={() => {}}
        allowedTypes={['examination']}
      />,
    );
    await waitFor(
      () => expect(screen.getByText('Blood Test')).toBeInTheDocument(),
      { timeout: 3000 },
    );
    expect(fetch).toHaveBeenCalled();
  });

  it('renders the footer selection count', () => {
    registerAdapter(makeAdapter());
    render(
      <InstanceBrowseModal
        isOpen
        onClose={() => {}}
        picked={[{ type: 'examination', id: 'e1', label: 'Blood Test' }]}
        onTogglePick={() => {}}
        allowedTypes={['examination']}
        patientId="patient-1"
      />,
    );
    expect(screen.getByText('1 selected')).toBeInTheDocument();
  });
});
