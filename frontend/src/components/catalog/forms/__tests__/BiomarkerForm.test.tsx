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
