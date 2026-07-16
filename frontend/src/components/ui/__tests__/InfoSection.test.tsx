import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Activity } from 'lucide-react';

import { InfoSection } from '../InfoSection';

describe('InfoSection', () => {
  it('renders the title and children when not collapsible', () => {
    render(
      <InfoSection title="Clinical">
        <span>body</span>
      </InfoSection>,
    );
    expect(screen.getByText('Clinical')).toBeInTheDocument();
    expect(screen.getByText('body')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('renders a leading icon when provided', () => {
    render(
      <InfoSection title="X" icon={Activity}>
        <span>b</span>
      </InfoSection>,
    );
    // icon svg is present (lucide renders an <svg>)
    const header = screen.getByText('X').parentElement!;
    expect(header.querySelector('svg')).toBeInTheDocument();
  });

  it('toggles open/closed via the header button when collapsible', () => {
    render(
      <InfoSection title="Meta" collapsible defaultOpen={false}>
        <span>hidden body</span>
      </InfoSection>,
    );
    const btn = screen.getByRole('button');
    expect(btn).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('hidden body')).not.toBeInTheDocument();
    fireEvent.click(btn);
    expect(btn).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('hidden body')).toBeInTheDocument();
  });

  it('is open by default when defaultOpen is true', () => {
    render(
      <InfoSection title="Meta" collapsible>
        <span>visible</span>
      </InfoSection>,
    );
    expect(screen.getByRole('button')).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('visible')).toBeInTheDocument();
  });

  it('links aria-controls to the body id', () => {
    render(
      <InfoSection title="Meta" collapsible>
        <span>x</span>
      </InfoSection>,
    );
    const btn = screen.getByRole('button');
    const bodyId = btn.getAttribute('aria-controls');
    expect(bodyId).toBeTruthy();
    expect(document.getElementById(bodyId!)).toBeInTheDocument();
  });
});
