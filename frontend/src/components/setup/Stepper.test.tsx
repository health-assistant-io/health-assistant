import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Stepper, type StepperStepView } from './Stepper';

function makeStep(overrides: Partial<StepperStepView> = {}): StepperStepView {
  return {
    id: 's1',
    title_i18n_key: 'setup.steps.s1',
    title: 'Step One',
    kind: 'inline_form',
    completed: false,
    optional: false,
    ...overrides,
  };
}

describe('Stepper', () => {
  it('renders each step title', () => {
    render(
      <Stepper
        steps={[makeStep(), makeStep({ id: 's2', title: 'Step Two' })]}
        activeStepId="s1"
        onSelect={() => {}}
      />,
    );
    expect(screen.getByText('Step One')).toBeInTheDocument();
    expect(screen.getByText('Step Two')).toBeInTheDocument();
  });

  it('renders the group label when provided', () => {
    render(
      <Stepper
        steps={[makeStep()]}
        activeStepId="s1"
        onSelect={() => {}}
        groupLabel="Patient profile"
      />,
    );
    expect(screen.getByText('Patient profile')).toBeInTheDocument();
  });

  it('marks the active step with aria-current', () => {
    render(
      <Stepper
        steps={[makeStep(), makeStep({ id: 's2', title: 'Step Two' })]}
        activeStepId="s2"
        onSelect={() => {}}
      />,
    );
    const activeBtn = screen.getByText('Step Two').closest('button');
    expect(activeBtn).toHaveAttribute('aria-current', 'step');
    const inactiveBtn = screen.getByText('Step One').closest('button');
    expect(inactiveBtn).not.toHaveAttribute('aria-current');
  });

  it('calls onSelect with the step id when a row is clicked', () => {
    const onSelect = vi.fn();
    render(
      <Stepper
        steps={[makeStep(), makeStep({ id: 's2', title: 'Step Two' })]}
        activeStepId="s1"
        onSelect={onSelect}
      />,
    );
    fireEvent.click(screen.getByText('Step Two'));
    expect(onSelect).toHaveBeenCalledWith('s2');
  });

  it('shows the "optional" hint only for optional + incomplete steps', () => {
    render(
      <Stepper
        steps={[
          makeStep({ id: 'opt', title: 'Optional One', optional: true, completed: false }),
          makeStep({ id: 'opt-done', title: 'Optional Done', optional: true, completed: true }),
          makeStep({ id: 'mand', title: 'Mandatory', optional: false, completed: false }),
        ]}
        activeStepId="mand"
        onSelect={() => {}}
      />,
    );
    // Only the incomplete optional step shows the "optional" sublabel.
    const optionalLabels = screen.getAllByText('optional');
    expect(optionalLabels).toHaveLength(1);
  });
});
