import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
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

vi.mock('../../../store/slices/patientSlice', () => ({
  usePatientStore: (sel: any) => sel({ currentPatient: { id: 'patient-1' } }),
}));

import { InstancePicker, selectionKey } from '../InstancePicker';
import {
  registerAdapter,
  _clearAdaptersForTests,
} from '../instanceRegistry';
import type { InstanceAdapter, InstanceSelection } from '../types';

const examAdapter: InstanceAdapter<any> = {
  type: 'examination',
  entityLabel: { singular: 'Examination', plural: 'Examinations' },
  icon: 'Stethoscope',
  fetch: async () => ({ items: [], total: 0 }),
  search: async () => [],
  fetchOne: async () => ({ id: '1', name: 'Fetched' }),
  facets: [],
  toRow: (item: any) => ({ id: item.id, type: 'examination', label: item.name, raw: item }),
  toSelection: (item: any) => ({ type: 'examination', id: item.id, label: item.name }),
  detailRoute: () => null,
};

describe('InstancePicker', () => {
  beforeEach(() => {
    _clearAdaptersForTests();
    registerAdapter(examAdapter);
  });
  afterEach(() => {
    _clearAdaptersForTests();
  });

  it('selectionKey dedups by type:id and relation', () => {
    const a: InstanceSelection = { type: 'examination', id: '1' };
    const b: InstanceSelection = { type: 'examination', id: '1', relation: 'X' };
    expect(selectionKey(a)).toBe('examination:1');
    expect(selectionKey(b)).toBe('examination:1:X');
    expect(selectionKey(a)).not.toBe(selectionKey(b));
  });

  it('renders the browse button and input', () => {
    render(<InstancePicker value={[]} onChange={() => {}} allowedTypes={['examination']} />);
    expect(screen.getByPlaceholderText('Search records…')).toBeInTheDocument();
    expect(screen.getByText('Browse')).toBeInTheDocument();
  });

  it('opens the browse modal when Browse is clicked', () => {
    render(<InstancePicker value={[]} onChange={() => {}} allowedTypes={['examination']} />);
    fireEvent.click(screen.getByText('Browse'));
    expect(screen.getByText('Browse Examinations')).toBeInTheDocument();
  });

  it('renders selected chips and removes one on X click', () => {
    const onChange = vi.fn();
    const value: InstanceSelection[] = [
      { type: 'examination', id: '1', label: 'Blood Test' },
      { type: 'examination', id: '2', label: 'X-Ray' },
    ];
    render(
      <InstancePicker value={value} onChange={onChange} allowedTypes={['examination']} />,
    );
    expect(screen.getByText('Blood Test')).toBeInTheDocument();
    expect(screen.getByText('X-Ray')).toBeInTheDocument();
    const removeButtons = screen.getAllByTitle('Remove');
    fireEvent.click(removeButtons[0]);
    expect(onChange).toHaveBeenCalledWith([
      { type: 'examination', id: '2', label: 'X-Ray' },
    ]);
  });

  it('disables the input when disabled', () => {
    render(
      <InstancePicker
        value={[]}
        onChange={() => {}}
        allowedTypes={['examination']}
        disabled
      />,
    );
    expect(screen.getByPlaceholderText('Search records…')).toHaveAttribute('disabled');
  });

  it('shows a browse-only placeholder when inline search is unavailable', () => {
    // Multi-type with no unifiedSearch -> inline search disabled.
    _clearAdaptersForTests();
    registerAdapter(examAdapter);
    const medAdapter: InstanceAdapter<any> = {
      ...examAdapter,
      type: 'medication',
      entityLabel: { singular: 'Medication', plural: 'Medications' },
      toRow: (item: any) => ({ id: item.id, type: 'medication', label: item.name, raw: item }),
    };
    registerAdapter(medAdapter);
    render(
      <InstancePicker
        value={[]}
        onChange={() => {}}
        allowedTypes={['examination', 'medication']}
      />,
    );
    expect(screen.getByPlaceholderText('Browse records…')).toBeInTheDocument();
  });
});
