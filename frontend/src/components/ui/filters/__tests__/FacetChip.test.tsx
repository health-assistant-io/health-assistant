import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, opts?: any) => opts?.defaultValue ?? k }),
}));

import { FacetChip } from '../FacetChip';
import type { FacetDefinition, FilterValue } from '../types';

interface Item {
  category: string;
  telemetry: boolean;
}

const multiFacet: FacetDefinition<Item> = {
  id: 'category',
  label: 'Category',
  kind: 'multi',
  mode: 'client',
};
const singleFacet: FacetDefinition<Item> = {
  id: 'source',
  label: 'Source',
  kind: 'single',
  mode: 'client',
};
const toggleFacet: FacetDefinition<Item> = {
  id: 'telemetry',
  label: 'Telemetry only',
  kind: 'toggle',
  mode: 'client',
  icon: 'Activity',
};

const options = [
  { value: 'Lipids', label: 'Lipids', count: 3 },
  { value: 'Glucose', label: 'Glucose', count: 2 },
  { value: 'Hormones', label: 'Hormones', count: 1 },
];

describe('FacetChip', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('toggle kind', () => {
    const off: FilterValue = { kind: 'toggle', on: false };
    const on: FilterValue = { kind: 'toggle', on: true };

    it('renders the label and is not active when off', () => {
      render(
        <FacetChip<Item>
          facet={toggleFacet}
          value={off}
          options={[]}
          onValueChange={vi.fn()}
          onToggleOption={vi.fn()}
        />,
      );
      const btn = screen.getByText('Telemetry only').closest('button')!;
      expect(btn).toBeInTheDocument();
      expect(btn.className).not.toMatch(/border-blue-500/);
    });

    it('shows active styling when on', () => {
      render(
        <FacetChip<Item>
          facet={toggleFacet}
          value={on}
          options={[]}
          onValueChange={vi.fn()}
          onToggleOption={vi.fn()}
        />,
      );
      const btn = screen.getByText('Telemetry only').closest('button')!;
      expect(btn.className).toMatch(/border-blue-500/);
    });

    it('calls onValueChange with flipped boolean when clicked', () => {
      const onValueChange = vi.fn();
      render(
        <FacetChip<Item>
          facet={toggleFacet}
          value={off}
          options={[]}
          onValueChange={onValueChange}
          onToggleOption={vi.fn()}
        />,
      );
      fireEvent.click(screen.getByText('Telemetry only'));
      expect(onValueChange).toHaveBeenCalledWith({ kind: 'toggle', on: true });
    });
  });

  describe('multi kind', () => {
    const empty: FilterValue = { kind: 'multi', values: [] };

    it('renders the facet label when nothing is selected', () => {
      render(
        <FacetChip<Item>
          facet={multiFacet}
          value={empty}
          options={options}
          onValueChange={vi.fn()}
          onToggleOption={vi.fn()}
        />,
      );
      expect(screen.getByText('Category')).toBeInTheDocument();
    });

    it('shows "N selected" when more than one option is chosen', () => {
      render(
        <FacetChip<Item>
          facet={multiFacet}
          value={{ kind: 'multi', values: ['Lipids', 'Glucose'] }}
          options={options}
          onValueChange={vi.fn()}
          onToggleOption={vi.fn()}
        />,
      );
      expect(screen.getByText('2 selected')).toBeInTheDocument();
    });

    it('opens the popover and lists options with counts', () => {
      render(
        <FacetChip<Item>
          facet={multiFacet}
          value={empty}
          options={options}
          onValueChange={vi.fn()}
          onToggleOption={vi.fn()}
        />,
      );
      fireEvent.click(screen.getByText('Category'));
      expect(screen.getByText('Lipids')).toBeInTheDocument();
      expect(screen.getByText('Glucose')).toBeInTheDocument();
      expect(screen.getByText('Hormones')).toBeInTheDocument();
      expect(screen.getByText('3')).toBeInTheDocument();
    });

    it('calls onToggleOption when an option is clicked', () => {
      const onToggleOption = vi.fn();
      render(
        <FacetChip<Item>
          facet={multiFacet}
          value={empty}
          options={options}
          onValueChange={vi.fn()}
          onToggleOption={onToggleOption}
        />,
      );
      fireEvent.click(screen.getByText('Category'));
      fireEvent.click(screen.getByText('Glucose'));
      expect(onToggleOption).toHaveBeenCalledWith('Glucose');
    });

    it('shows an empty-state when there are no options', () => {
      render(
        <FacetChip<Item>
          facet={multiFacet}
          value={empty}
          options={[]}
          onValueChange={vi.fn()}
          onToggleOption={vi.fn()}
        />,
      );
      fireEvent.click(screen.getByText('Category'));
      expect(screen.getByText('No options')).toBeInTheDocument();
    });
  });

  describe('single kind', () => {
    it('shows the selected option label on the trigger', () => {
      render(
        <FacetChip<Item>
          facet={singleFacet}
          value={{ kind: 'single', value: 'Lipids' }}
          options={options}
          onValueChange={vi.fn()}
          onToggleOption={vi.fn()}
          showActivePills={false}
        />,
      );
      expect(screen.getByText('Lipids')).toBeInTheDocument();
    });

    it('calls onToggleOption with the chosen value', () => {
      const onToggleOption = vi.fn();
      render(
        <FacetChip<Item>
          facet={singleFacet}
          value={{ kind: 'single', value: null }}
          options={options}
          onValueChange={vi.fn()}
          onToggleOption={onToggleOption}
        />,
      );
      fireEvent.click(screen.getByText('Source'));
      fireEvent.click(screen.getByText('Hormones'));
      expect(onToggleOption).toHaveBeenCalledWith('Hormones');
    });
  });

  describe('active pills', () => {
    it('renders a removable pill for each selected option (desktop)', () => {
      const onToggleOption = vi.fn();
      render(
        <FacetChip<Item>
          facet={multiFacet}
          value={{ kind: 'multi', values: ['Lipids'] }}
          options={options}
          onValueChange={vi.fn()}
          onToggleOption={onToggleOption}
          showActivePills
        />,
      );
      const removeBtn = screen.getByLabelText('Remove Lipids');
      fireEvent.click(removeBtn);
      expect(onToggleOption).toHaveBeenCalledWith('Lipids');
    });
  });
});
