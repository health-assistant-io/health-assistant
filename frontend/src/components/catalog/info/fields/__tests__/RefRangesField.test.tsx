import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { RefRangesField } from '../RefRangesField';

describe('RefRangesField', () => {
  it('renders a muted dash when value is not an array', () => {
    const { container } = render(<RefRangesField value={null} />);
    expect(container.textContent).toBe('—');
  });

  it('renders a muted dash for an empty array', () => {
    const { container } = render(<RefRangesField value={[]} />);
    expect(container.textContent).toBe('—');
  });

  it('renders one row per range with sex/age/range columns', () => {
    const ranges = [
      { sex: 'MALE', age_min: 18, age_max: 65, low: 4.5, high: 6.0, text: '', applies_to: null },
      { sex: null, age_min: null, age_max: null, low: null, high: null, text: 'Normal', applies_to: 'pregnant' },
    ];
    render(<RefRangesField value={ranges} />);
    // header cells
    expect(screen.getByText('Sex')).toBeInTheDocument();
    expect(screen.getByText('Age')).toBeInTheDocument();
    expect(screen.getByText('Range')).toBeInTheDocument();
    // row 1: MALE, age, low–high range (String(6.0) === '6')
    expect(screen.getByText('MALE')).toBeInTheDocument();
    expect(screen.getAllByText('4.5 – 6').length).toBeGreaterThanOrEqual(1);
    // row 2: Any sex, Any age, text-based range, applies_to note
    expect(screen.getAllByText('Any').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('Normal')).toBeInTheDocument();
    expect(screen.getByText('pregnant')).toBeInTheDocument();
  });

  it('shows "Any" for sex and age when those are null', () => {
    render(
      <RefRangesField
        value={[{ sex: null, age_min: null, age_max: null, low: 1, high: 2, text: '', applies_to: null }]}
      />,
    );
    expect(screen.getAllByText('Any').length).toBeGreaterThanOrEqual(2);
  });

  it('renders a summary number-line band when a numeric low+high range exists', () => {
    const range = {
      sex: 'MALE',
      age_min: null,
      age_max: null,
      low: 4,
      high: 6,
      text: '',
      applies_to: null,
    };
    render(<RefRangesField value={[range]} />);
    // RangeBar renders the "low – high" label (the table cell also shows it).
    expect(screen.getAllByText('4 – 6').length).toBeGreaterThanOrEqual(1);
  });

  it('omits the number-line when no range has numeric low+high', () => {
    const range = {
      sex: null,
      age_min: null,
      age_max: null,
      low: null,
      high: null,
      text: 'Normal',
      applies_to: null,
    };
    render(<RefRangesField value={[range]} />);
    expect(screen.queryByText('Normal – Normal')).not.toBeInTheDocument();
  });
});
