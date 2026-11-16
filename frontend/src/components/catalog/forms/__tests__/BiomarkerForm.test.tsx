import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

// Stub i18n — return the fallback string (the form uses t(key, fallback).
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback?: string) => fallback ?? 't-key' }),
}));

// Stub services so the form mounts without network.
vi.mock('../../../services/biomarkerService', () => ({
  __esModule: true,
  default: {
    getUnits: vi.fn().mockResolvedValue([]),
    getStates: vi.fn().mockResolvedValue([]),
  },
}));

// Stub child editors so we can focus on the discriminator toggle.
vi.mock('../AllowedStatesEditor', () => ({
  AllowedStatesEditor: () => <div data-testid="states-editor" />,
}));
vi.mock('../ReferenceRangesEditor', () => ({
  ReferenceRangesEditor: () => <div data-testid="ranges-editor" />,
}));
vi.mock('../../ui/RichTextEditor', () => ({
  RichTextEditor: () => <div />,
}));
vi.mock('../../ui/CodingSystemSelect', () => ({
  CodingSystemSelect: () => <div />,
}));
vi.mock('../../ai/hitl/LinksSection', () => ({
  LinksSection: () => null,
}));

import { BiomarkerForm } from '../BiomarkerForm';

describe('BiomarkerForm value-type discriminator', () => {
  it('renders the Quantity/State toggle for a fresh biomarker', () => {
    const onChange = vi.fn();
    render(<BiomarkerForm values={{ name: '' }} onChange={onChange} mode="create" />);
    expect(screen.getByText('Quantity (numeric)')).toBeInTheDocument();
    expect(screen.getByText('State (categorical)')).toBeInTheDocument();
  });

  it('shows reference-range editors when value_type is quantity (default)', () => {
    const onChange = vi.fn();
    render(<BiomarkerForm values={{ name: '' }} onChange={onChange} mode="create" />);
    expect(screen.getByText('Ref. min')).toBeInTheDocument();
    expect(screen.getByText('Ref. max')).toBeInTheDocument();
    expect(screen.getByText('Preferred unit')).toBeInTheDocument();
    expect(screen.queryByTestId('states-editor')).not.toBeInTheDocument();
  });

  it('switches to the allowed-states editor when State is clicked', () => {
    const onChange = vi.fn();
    render(<BiomarkerForm values={{ name: '' }} onChange={onChange} mode="create" />);
    fireEvent.click(screen.getByText('State (categorical)'));
    // The form must emit a value_type=state patch so the parent re-renders
    // the form with the state-only branch.
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ value_type: 'state' }));
  });

  it('renders the allowed-states editor when value_type is already state', () => {
    render(<BiomarkerForm values={{ name: 'Sars-CoV-2', value_type: 'state', allowed_states: [] }} onChange={vi.fn()} mode="edit" />);
    expect(screen.getByTestId('states-editor')).toBeInTheDocument();
    expect(screen.queryByText('Ref. min')).not.toBeInTheDocument();
  });
});

describe('BiomarkerForm slug ownership', () => {
  // The Name input is the first textbox in the DOM (the slug field is second).
  const nameInput = () => screen.getAllByRole('textbox')[0] as HTMLInputElement;
  const slugInput = () =>
    screen.getByPlaceholderText('auto-generated-from-name') as HTMLInputElement;

  it('keeps the slug editable by default (the workspace explicit-slug door)', () => {
    const onChange = vi.fn();
    render(<BiomarkerForm values={{ name: 'Ferritin' }} onChange={onChange} mode="create" />);
    expect(slugInput()).toBeEnabled();
    expect(screen.getByText('Auto-generated from name — edit to override')).toBeInTheDocument();
    fireEvent.change(slugInput(), { target: { value: 'ferritin-custom' } });
    expect(onChange).toHaveBeenCalledWith({ slug: 'ferritin-custom' });
  });

  it('disables the slug field when the server owns identity (serverOwnedSlug)', () => {
    const onChange = vi.fn();
    render(
      <BiomarkerForm
        values={{ name: 'Ferritin' }}
        onChange={onChange}
        mode="create"
        serverOwnedSlug
      />,
    );
    // No editable slug: the dialog strips it from the wire, so an edit there
    // would be silently dropped — the field is disabled instead.
    expect(slugInput()).toBeDisabled();
    expect(screen.queryByText('Auto-generated from name — edit to override')).not.toBeInTheDocument();
    expect(screen.getByText('Derived from the name on save')).toBeInTheDocument();
  });

  it('still derives the slug for display from the name while server-owned', () => {
    const onChange = vi.fn();
    render(<BiomarkerForm values={{ name: '' }} onChange={onChange} mode="create" serverOwnedSlug />);
    fireEvent.change(nameInput(), { target: { value: 'Fasting Glucose' } });
    // The display-only derivation still runs (the dialog shows what the
    // backend will derive) and the slug field itself emits no edit.
    expect(onChange).toHaveBeenCalledWith({ name: 'Fasting Glucose', slug: 'fasting-glucose' });
    expect(slugInput()).toBeDisabled();
  });
});
