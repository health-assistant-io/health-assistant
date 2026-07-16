import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { RangeBar } from '../RangeBar';

describe('RangeBar', () => {
  it('renders nothing for an inverted or non-finite range', () => {
    const { container, rerender } = render(<RangeBar low={6} high={4} />);
    expect(container.firstChild).toBeNull();
    rerender(<RangeBar low={NaN} high={10} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders the low–high label with the band', () => {
    render(<RangeBar low={4} high={6} />);
    expect(screen.getByText('4 – 6')).toBeInTheDocument();
  });

  it('appends the unit when provided', () => {
    render(<RangeBar low={4} high={6} unit="mg/dL" />);
    expect(screen.getByText('4 – 6 mg/dL')).toBeInTheDocument();
  });

  it('clamps the domain floor to 0 when the padded domain would go negative', () => {
    // low=1, high=3 → padded domain [-1,5] → floor clamped to 0 → [0,6]
    render(<RangeBar low={1} high={3} />);
    expect(screen.getByText('0')).toBeInTheDocument();
  });

  it('respects explicit min/max domain bounds', () => {
    render(<RangeBar low={4} high={6} min={0} max={10} />);
    expect(screen.getByText('0')).toBeInTheDocument();
    expect(screen.getByText('10')).toBeInTheDocument();
  });

  it('renders a value marker dot when value is provided', () => {
    const { container } = render(<RangeBar low={4} high={6} value={5} />);
    const marker = container.querySelector('.bg-blue-600');
    expect(marker).toBeInTheDocument();
  });

  it('omits the marker when value is null', () => {
    const { container } = render(<RangeBar low={4} high={6} value={null} />);
    expect(container.querySelector('.bg-blue-600')).not.toBeInTheDocument();
  });
});
