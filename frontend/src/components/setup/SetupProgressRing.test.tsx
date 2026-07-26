import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SetupProgressRing } from './SetupProgressRing';

describe('SetupProgressRing', () => {
  it('renders 0% at value 0', () => {
    render(<SetupProgressRing value={0} />);
    expect(screen.getByText('0%')).toBeInTheDocument();
  });

  it('renders 50% at value 0.5', () => {
    render(<SetupProgressRing value={0.5} />);
    expect(screen.getByText('50%')).toBeInTheDocument();
  });

  it('renders 100% at value 1', () => {
    render(<SetupProgressRing value={1} />);
    expect(screen.getByText('100%')).toBeInTheDocument();
  });

  it('clamps values below 0 to 0%', () => {
    render(<SetupProgressRing value={-0.5} />);
    expect(screen.getByText('0%')).toBeInTheDocument();
  });

  it('clamps values above 1 to 100%', () => {
    render(<SetupProgressRing value={1.5} />);
    expect(screen.getByText('100%')).toBeInTheDocument();
  });

  it('uses the explicit label when provided', () => {
    render(<SetupProgressRing value={0.3} label="Done" />);
    expect(screen.getByText('Done')).toBeInTheDocument();
    expect(screen.queryByText('30%')).not.toBeInTheDocument();
  });
});
