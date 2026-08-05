import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { AllowedStatesField } from '../AllowedStatesField';

describe('AllowedStatesField', () => {
  it('renders a muted dash when value is null', () => {
    const { container } = render(<AllowedStatesField value={null} />);
    expect(container.textContent).toBe('—');
  });

  it('renders a muted dash for an empty array', () => {
    const { container } = render(<AllowedStatesField value={[]} />);
    expect(container.textContent).toBe('—');
  });

  it('renders one chip per state and lists the totals', () => {
    const states = [
      { state_id: 's1', state_slug: 'pos', code: 'POS', system: 'http://hl7.org', display: 'Positive', is_normal: false, sort_order: 2 },
      { state_id: 's2', state_slug: 'neg', code: 'NEG', system: 'http://hl7.org', display: 'Negative', is_normal: true, sort_order: 1 },
    ];
    render(<AllowedStatesField value={states} />);

    expect(screen.getByText('Positive')).toBeInTheDocument();
    expect(screen.getByText('Negative')).toBeInTheDocument();
    // Totals summary reads "1 normal · 2 total".
    expect(screen.getByText(/1 normal/i)).toBeInTheDocument();
    expect(screen.getByText(/2 total/i)).toBeInTheDocument();
  });

  it('sorts the normal set first so it reads as a header band', () => {
    const states = [
      { state_id: 's1', state_slug: 'pos', code: 'POS', system: '', display: 'Positive', is_normal: false, sort_order: 1 },
      { state_id: 's2', state_slug: 'neg', code: 'NEG', system: '', display: 'Negative', is_normal: true, sort_order: 2 },
    ];
    const { container } = render(<AllowedStatesField value={states} />);
    // Target only the outer chip spans (they carry the `inline-flex` class —
    // the inner spans don't). This avoids catching nested icon/text spans.
    const chips = Array.from(container.querySelectorAll('span.inline-flex'));
    expect(chips.length).toBe(2);
    // Negative (normal) should render before Positive despite later sort_order.
    expect(chips[0].textContent).toContain('Negative');
    expect(chips[1].textContent).toContain('Positive');
  });

  it('falls back to code/state_slug when display is missing', () => {
    const states = [
      { state_id: 's1', state_slug: 'trace', code: 'TR', system: '', display: '', is_normal: false, sort_order: 1 },
    ];
    render(<AllowedStatesField value={states} />);
    // When display is empty, the chip uses code as the primary label.
    expect(screen.getAllByText((_, el) => !!el?.textContent?.includes('TR')).length).toBeGreaterThan(0);
  });

  it('is defensive against non-array inputs (numbers, objects, strings)', () => {
    const { container } = render(<AllowedStatesField value={42} />);
    expect(container.textContent).toBe('—');
  });
});
