import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { DoseScheduleField } from '../DoseScheduleField';

describe('DoseScheduleField', () => {
  it('renders a muted dash when value is null', () => {
    const { container } = render(<DoseScheduleField value={null} />);
    expect(container.textContent).toBe('—');
  });

  it('renders a muted dash when both doses and intervals are empty', () => {
    const { container } = render(<DoseScheduleField value={{ doses: null, intervals: [] }} />);
    expect(container.textContent).toBe('—');
  });

  it('renders the dose count with singular/plural noun', () => {
    const { rerender } = render(<DoseScheduleField value={{ doses: 1, intervals: [] }} />);
    expect(screen.getByText('1')).toBeInTheDocument();
    expect(screen.getByText('dose')).toBeInTheDocument();
    rerender(<DoseScheduleField value={{ doses: 3, intervals: [] }} />);
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('doses')).toBeInTheDocument();
  });

  it('renders intervals as chips', () => {
    render(<DoseScheduleField value={{ doses: 2, intervals: ['0 months', '6 months'] }} />);
    expect(screen.getByText('0 months')).toBeInTheDocument();
    expect(screen.getByText('6 months')).toBeInTheDocument();
    expect(screen.getByText('Intervals')).toBeInTheDocument();
  });

  it('renders only intervals when doses is null', () => {
    render(<DoseScheduleField value={{ doses: null, intervals: ['4 weeks'] }} />);
    expect(screen.getByText('4 weeks')).toBeInTheDocument();
    expect(screen.queryByText('doses')).not.toBeInTheDocument();
  });
});
