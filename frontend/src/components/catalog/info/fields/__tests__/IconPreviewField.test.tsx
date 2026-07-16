import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { IconPreviewField } from '../IconPreviewField';

describe('IconPreviewField', () => {
  it('renders a muted dash when value is empty', () => {
    const { container } = render(<IconPreviewField value={null} />);
    expect(container.textContent).toBe('—');
  });

  it('renders a lucide icon descriptor as a glyph + mono caption', () => {
    render(<IconPreviewField value={{ type: 'lucide', value: 'droplet' }} />);
    expect(screen.getByText('droplet')).toBeInTheDocument();
    // a lucide glyph renders as an <svg>
    expect(document.querySelector('svg')).toBeInTheDocument();
  });

  it('accepts a legacy string icon', () => {
    render(<IconPreviewField value="activity" />);
    expect(screen.getByText('activity')).toBeInTheDocument();
    expect(document.querySelector('svg')).toBeInTheDocument();
  });

  it('renders a dash for a malformed descriptor (no value)', () => {
    const { container } = render(<IconPreviewField value={{ type: 'lucide' }} />);
    expect(container.textContent).toBe('—');
  });

  it('renders without error when a color tint is provided', () => {
    const { container } = render(
      <IconPreviewField value={{ type: 'lucide', value: 'droplet' }} color="#ff0000" />,
    );
    // glyph still renders; lucide applies `color` internally (not asserted).
    expect(container.querySelector('svg')).toBeInTheDocument();
    expect(screen.getByText('droplet')).toBeInTheDocument();
  });
});
