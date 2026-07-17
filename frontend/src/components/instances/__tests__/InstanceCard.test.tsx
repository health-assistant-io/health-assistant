import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { fireEvent } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, dft?: any) => dft ?? k,
    i18n: { language: 'en' },
  }),
}));

import { InstanceCard, invalidateInstanceCard } from '../InstanceCard';
import {
  registerAdapter,
  _clearAdaptersForTests,
} from '../instanceRegistry';
import {
  registerInstanceDetail,
  _clearDetailsForTests,
  type InstanceDetailProps,
} from '../detailViewRegistry';
import type { InstanceAdapter } from '../types';

const item = {
  id: 'e1',
  name: 'Blood Test',
  category: 'Lab',
  date: '2026-01-02T00:00:00Z',
  status: 'completed',
};

const adapter: InstanceAdapter<any> = {
  type: 'examination',
  entityLabel: { singular: 'Examination', plural: 'Examinations' },
  icon: 'Stethoscope',
  fetch: async () => ({ items: [item], total: 1 }),
  fetchOne: vi.fn(async () => item),
  search: async () => [],
  facets: [],
  toRow: (it: any) => ({
    id: it.id,
    type: 'examination',
    label: it.name,
    subtitle: it.category,
    date: it.date,
    status: it.status,
    icon: 'Stethoscope',
    raw: it,
  }),
  toSelection: (it: any) => ({ type: 'examination', id: it.id, label: it.name }),
  detailRoute: (it: any) => `/examinations/${it.id}`,
};

describe('InstanceCard', () => {
  beforeEach(() => {
    _clearAdaptersForTests();
    registerAdapter(adapter);
    (adapter.fetchOne as any).mockClear();
    invalidateInstanceCard('examination', 'e1');
  });
  afterEach(() => {
    _clearAdaptersForTests();
    _clearDetailsForTests();
  });

  it('resolves via adapter.fetchOne + toRow and renders label/type/status', async () => {
    render(<InstanceCard selection={{ type: 'examination', id: 'e1' }} patientId="p1" />);
    await waitFor(() => {
      expect(screen.getByText('Blood Test')).toBeInTheDocument();
    });
    expect(adapter.fetchOne).toHaveBeenCalledWith('e1', 'p1');
    expect(screen.getByText('examination')).toBeInTheDocument();
    expect(screen.getByText('completed')).toBeInTheDocument();
    expect(screen.getByText('Lab')).toBeInTheDocument();
    // The "open in domain" affordance is a button (not a link) — it opens an
    // in-app overlay so the caller form/modal is never navigated away from
    // (safe in the standalone PWA, where a new tab would exit the app).
    const openBtn = screen.getByTitle('Open in domain view');
    expect(openBtn.tagName).toBe('BUTTON');
    fireEvent.click(openBtn);
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toBeInTheDocument();
    // The overlay shows the resolved record label.
    expect(screen.getAllByText('Blood Test').length).toBeGreaterThan(0);
  });

  it('renders the remove button and fires onRemove', async () => {
    const onRemove = vi.fn();
    render(
      <InstanceCard
        selection={{ type: 'examination', id: 'e1' }}
        patientId="p1"
        onRemove={onRemove}
      />,
    );
    await waitFor(() => expect(screen.getByText('Blood Test')).toBeInTheDocument());
    fireEvent.click(screen.getByTitle('Remove'));
    expect(onRemove).toHaveBeenCalledOnce();
  });

  it('shows the unavailable fallback when fetchOne rejects', async () => {
    (adapter.fetchOne as any).mockRejectedValueOnce(new Error('boom'));
    invalidateInstanceCard('examination', 'missing');
    render(
      <InstanceCard
        selection={{ type: 'examination', id: 'missing', label: 'Cached Label' }}
        patientId="p1"
      />,
    );
    await waitFor(() => {
      expect(screen.getByText('Record unavailable')).toBeInTheDocument();
    });
    expect(screen.getByText('Cached Label')).toBeInTheDocument();
  });

  it('overlay renders a registered per-type detail view (single source of truth) instead of the generic fallback', async () => {
    const Detail = ({ id }: InstanceDetailProps) => (
      <div>MOCK RICH DETAIL {id}</div>
    );
    registerInstanceDetail('examination', Detail);

    render(<InstanceCard selection={{ type: 'examination', id: 'e1' }} patientId="p1" />);
    await waitFor(() => expect(screen.getByText('Blood Test')).toBeInTheDocument());
    fireEvent.click(screen.getByTitle('Open in domain view'));

    expect(await screen.findByText('MOCK RICH DETAIL e1')).toBeInTheDocument();
  });
});
