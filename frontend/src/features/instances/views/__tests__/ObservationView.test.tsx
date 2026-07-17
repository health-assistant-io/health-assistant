import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: any) => (typeof d === 'string' ? d : k) }),
  initReactI18next: { type: '3rdParty', init: () => {} },
}));

const biomarkers = [
  {
    id: 'o1',
    displayName: 'Glucose',
    value: { raw: 5.4, normalized: 5.4 },
    unit: { rawSymbol: 'mmol/L', normalizedSymbol: 'mmol/L' },
    referenceRange: { displayText: '3.9 - 5.5' },
    interpretation: 'Normal',
    source: { date: '2026-01-02T00:00:00Z' },
    definitionId: 'b1',
  },
  {
    id: 'o2',
    displayName: 'Cholesterol',
    value: { raw: 7.1, normalized: 7.1 },
    unit: { rawSymbol: 'mmol/L', normalizedSymbol: 'mmol/L' },
    referenceRange: { displayText: '< 5.0' },
    interpretation: 'High',
    source: { date: '2026-01-03T00:00:00Z' },
    definitionId: 'b2',
  },
];

vi.mock('../../../../hooks/useBiomarkers', () => ({
  useBiomarkers: () => ({ biomarkers }),
}));

import { ObservationView } from '../ObservationView';
import type { Observation } from '../../../../types/observation';

const items: Observation[] = [
  { id: 'o1' } as any,
  { id: 'o2' } as any,
];

describe('ObservationView', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders one biomarker card per normalized biomarker', () => {
    render(
      <ObservationView
        items={items}
        pickedIds={[]}
        onTogglePick={() => {}}
        loading={false}
        hasMore={false}
        loadingMore={false}
        onLoadMore={() => {}}
      />,
    );
    expect(screen.getByText('Glucose')).toBeInTheDocument();
    expect(screen.getByText('Cholesterol')).toBeInTheDocument();
  });

  it('marks a picked biomarker and fires onTogglePick with the raw observation', () => {
    const onTogglePick = vi.fn();
    render(
      <ObservationView
        items={items}
        pickedIds={['o1']}
        onTogglePick={onTogglePick}
        loading={false}
        hasMore={false}
        loadingMore={false}
        onLoadMore={() => {}}
      />,
    );
    // o1 is picked -> Added; the other -> Add.
    expect(screen.getAllByText('Added').length).toBeGreaterThanOrEqual(1);
    const addBtns = screen.getAllByText('Add');
    fireEvent.click(addBtns[0]);
    expect(onTogglePick).toHaveBeenCalledOnce();
    expect((onTogglePick.mock.calls[0] as any)[0].id).toBe('o2');
  });
});
