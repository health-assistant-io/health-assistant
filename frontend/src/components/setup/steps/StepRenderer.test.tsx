import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { StepRenderer } from './StepRenderer';
import type { SetupStep } from '../../../types/setup';

// i18n is not initialised in the test env; mock it to return a lookup or the key.
const STRINGS: Record<string, string> = {
  'setup.action_open': 'Open',
  'setup.action_manage': 'Manage',
  'setup.optional': 'optional',
  'setup.redirect_hint_complete': 'Completed — you can review or change these settings anytime.',
  'setup.redirect_hint_incomplete': 'Complete this step on the target page — it will be detected here automatically.',
};
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => STRINGS[key] ?? key }),
}));

function makeStep(overrides: Partial<SetupStep> = {}): SetupStep {
  return {
    id: 's1',
    title_i18n_key: 'setup.steps.s1',
    kind: 'derived',
    completed: false,
    optional: false,
    ...overrides,
  };
}

describe('StepRenderer', () => {
  it('renders RedirectStep for kind=redirect with an "Open" CTA when incomplete', () => {
    const onNavigate = vi.fn();
    render(
      <StepRenderer
        step={makeStep({
          kind: 'redirect',
          payload_hint: { route: '/patients?new=patient' },
        })}
        title="Create a patient"
        onNavigate={onNavigate}
      />,
    );
    const cta = screen.getByText('Open');
    fireEvent.click(cta);
    expect(onNavigate).toHaveBeenCalledWith('/patients?new=patient');
  });

  it('renders a "Manage" CTA on a completed redirect step (reopen settings)', () => {
    const onNavigate = vi.fn();
    render(
      <StepRenderer
        step={makeStep({
          kind: 'external_config',
          completed: true,
          payload_hint: { route: '/admin/system/ai-config' },
        })}
        title="Configure system AI"
        onNavigate={onNavigate}
      />,
    );
    // Completed → "Manage" button (not "Open").
    const manageBtn = screen.getByText('Manage');
    fireEvent.click(manageBtn);
    expect(onNavigate).toHaveBeenCalledWith('/admin/system/ai-config');
    // And the completed hint is shown.
    expect(screen.getByText(/Completed — you can review/i)).toBeInTheDocument();
  });

  it('renders DerivedStep for kind=derived', () => {
    render(
      <StepRenderer
        step={makeStep({ kind: 'derived', completed: true })}
        title="Catalog seeded"
      />,
    );
    expect(screen.getByText('Catalog seeded')).toBeInTheDocument();
  });

  it('renders the inline-form renderer when provided', () => {
    render(
      <StepRenderer
        step={makeStep({ kind: 'inline_form' })}
        title="Demographics"
        renderInlineForm={() => <div data-testid="custom-section">demographics form</div>}
      />,
    );
    expect(screen.getByTestId('custom-section')).toBeInTheDocument();
  });

  it('falls back to DerivedStep for inline_form when no renderer is provided', () => {
    render(
      <StepRenderer step={makeStep({ kind: 'inline_form' })} title="Demographics" />,
    );
    expect(screen.getByText('Demographics')).toBeInTheDocument();
  });

  it('falls back to DerivedStep for an unknown kind (never crashes)', () => {
    render(
      <StepRenderer
        step={makeStep({ kind: 'future_kind' as any })}
        title="Future Step"
      />,
    );
    expect(screen.getByText('Future Step')).toBeInTheDocument();
  });
});
