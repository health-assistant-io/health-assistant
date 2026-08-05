import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { StateTimeline } from '../StateTimeline';

describe('StateTimeline', () => {
  it('renders nothing for an empty points array', () => {
    const { container } = render(<StateTimeline points={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders one legend chip per unique state + a summary line', () => {
    const points = [
      { date: '2024-01-01T00:00:00Z', state: 'POS', state_display: 'Positive', state_is_normal: false },
      { date: '2024-02-01T00:00:00Z', state: 'NEG', state_display: 'Negative', state_is_normal: true },
      { date: '2024-03-01T00:00:00Z', state: 'POS', state_display: 'Positive', state_is_normal: false },
    ];
    render(<StateTimeline points={points} />);

    // Two unique states → two legend chips. Both labels render.
    expect(screen.getAllByText('Positive').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Negative').length).toBeGreaterThan(0);
    // The summary footer reads the observation count.
    expect(screen.getByText(/3 observations/i)).toBeInTheDocument();
    expect(screen.getByText(/2 unique states/i)).toBeInTheDocument();
  });

  it('emits an SVG with dots + step-line segments between observations', () => {
    const points = [
      { date: '2024-01-01T00:00:00Z', state: 'POS', state_display: 'Positive', state_is_normal: false },
      { date: '2024-02-01T00:00:00Z', state: 'NEG', state_display: 'Negative', state_is_normal: true },
    ];
    const { container } = render(<StateTimeline points={points} />);

    // The legend's lucide icons are also <svg>; scope to the timeline via
    // the role="img" attribute set on the timeline svg only.
    const svg = container.querySelector('svg[role="img"]')!;
    expect(svg).toBeTruthy();
    // Two observations → two dots.
    expect(svg.querySelectorAll('circle').length).toBe(2);
    // One connector between the two (n-1 segments).
    expect(svg.querySelectorAll('path').length).toBe(1);
  });

  it('falls back to value/code when state_display is missing', () => {
    const points = [
      { date: '2024-01-01T00:00:00Z', state: 'DET', state_is_normal: false },
    ];
    render(<StateTimeline points={points} />);
    // No display → falls back to bare code "DET" as the label.
    expect(screen.getAllByText(/DET/).length).toBeGreaterThan(0);
    expect(screen.getByText(/1 observation/i)).toBeInTheDocument();
  });

  it('renders a single-observation timeline without connectors', () => {
    const points = [
      { date: '2024-01-01T00:00:00Z', state: 'NEG', state_display: 'Negative', state_is_normal: true },
    ];
    const { container } = render(<StateTimeline points={points} />);
    const svg = container.querySelector('svg[role="img"]')!;
    expect(svg.querySelectorAll('circle').length).toBe(1);
    expect(svg.querySelectorAll('path').length).toBe(0); // no connectors
  });
});
