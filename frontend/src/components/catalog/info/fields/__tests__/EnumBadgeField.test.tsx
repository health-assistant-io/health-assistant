import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { EnumBadgeField } from '../EnumBadgeField';

const OPTIONS: Record<string, string> = {
  draft: 'Draft',
  active: 'Active',
  retired: 'Retired',
};

describe('EnumBadgeField', () => {
  it('renders a muted dash when value is empty', () => {
    render(<EnumBadgeField value="" options={OPTIONS} />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('resolves the label from options', () => {
    render(<EnumBadgeField value="active" options={OPTIONS} />);
    expect(screen.getByText('Active')).toBeInTheDocument();
  });

  it('case-insensitive option lookup (ALLERGY upper vs option keys)', () => {
    render(
      <EnumBadgeField
        value="FOOD"
        options={{ food: 'Food', medication: 'Medication' }}
      />,
    );
    expect(screen.getByText('Food')).toBeInTheDocument();
  });

  it('falls back to the raw value when no option matches', () => {
    render(<EnumBadgeField value="unknown" options={OPTIONS} />);
    expect(screen.getByText('unknown')).toBeInTheDocument();
  });

  it('applies a per-value tone when provided (active → success)', () => {
    render(
      <EnumBadgeField
        value="active"
        options={OPTIONS}
        tones={{ draft: 'warning', active: 'success', retired: 'neutral' }}
      />,
    );
    expect(screen.getByText('Active').className).toContain('bg-emerald-50');
  });

  it('uses defaultTone for values without an explicit tone', () => {
    render(<EnumBadgeField value="retired" options={OPTIONS} defaultTone="neutral" />);
    expect(screen.getByText('Retired').className).toContain('bg-gray-100');
  });
});
