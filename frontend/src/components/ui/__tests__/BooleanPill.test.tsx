import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { BooleanPill } from '../BooleanPill';

describe('BooleanPill', () => {
  it('renders the "on" label and green classes for truthy value', () => {
    render(<BooleanPill value={true} labelOn="Telemetry" labelOff="Off" />);
    const pill = screen.getByText('Telemetry');
    expect(pill.className).toContain('bg-emerald-50');
  });

  it('renders the "off" label and muted classes for falsy value', () => {
    render(<BooleanPill value={false} labelOn="On" labelOff="No data" />);
    const pill = screen.getByText('No data');
    expect(pill.className).toContain('bg-gray-100');
  });

  it('treats null as falsy', () => {
    render(<BooleanPill value={null} />);
    expect(screen.getByText('No')).toBeInTheDocument();
  });

  it('uses default Yes/No labels', () => {
    const { rerender } = render(<BooleanPill value={true} />);
    expect(screen.getByText('Yes')).toBeInTheDocument();
    rerender(<BooleanPill value={false} />);
    expect(screen.getByText('No')).toBeInTheDocument();
  });
});
