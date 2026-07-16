import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    // Faithful to i18next: t(key, 'default') and t(key, {defaultValue, ...vars}),
    // interpolating {{var}} placeholders from the options object.
    t: (k: string, opts?: any) => {
      if (typeof opts === 'string') return opts;
      let s = opts?.defaultValue ?? k;
      if (opts && typeof opts === 'object') {
        for (const [key, val] of Object.entries(opts)) {
          if (key === 'defaultValue') continue;
          s = s.replace(new RegExp(`{{\\s*${key}\\s*}}`, 'g'), String(val));
        }
      }
      return s;
    },
    i18n: { language: 'en' },
  }),
}));
vi.mock('react-toastify', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { CatalogItemInfo } from '../CatalogItemInfo';
import type { CatalogItem } from '../../../../types/catalog';

describe('CatalogItemInfo', () => {
  beforeEach(() => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      configurable: true,
      writable: true,
    });
  });

  it('renders the empty-state when item is null', () => {
    render(<CatalogItemInfo item={null} total={5} hideHeader />);
    expect(screen.getByText(/No item selected/i)).toBeInTheDocument();
  });

  it('survives transitioning from no-item to an item without a hooks-order error', () => {
    // Regression: hooks must be called unconditionally. Previously the filter
    // useState/useMemo sat after the `if (!item)` return, so selecting an item
    // changed the hook count and crashed ("Rendered more hooks than during the
    // previous render").
    const { rerender } = render(<CatalogItemInfo item={null} total={1} catalogType="biomarker" hideHeader />);
    const item = { name: 'Glucose', slug: 'glucose', code: '2345-7', coding_system: 'loinc', info: 'x', preferred_unit_symbol: 'mg/dL' } as CatalogItem;
    rerender(<CatalogItemInfo item={item} total={1} catalogType="biomarker" hideHeader />);
    expect(screen.getByText('Glucose')).toBeInTheDocument();
    // and back to empty
    rerender(<CatalogItemInfo item={null} total={1} catalogType="biomarker" hideHeader />);
    expect(screen.getByText(/No item selected/i)).toBeInTheDocument();
  });

  it('renders registry sections for a biomarker item', () => {
    const item = {
      name: 'Glucose',
      slug: 'glucose',
      code: '2345-7',
      coding_system: 'loinc',
      aliases: ['FBS'],
      preferred_unit_symbol: 'mg/dL',
    } as CatalogItem;
    render(<CatalogItemInfo item={item} total={1} catalogType="biomarker" hideHeader />);
    expect(screen.getByText('Identity')).toBeInTheDocument();
    expect(screen.getByText('Coding')).toBeInTheDocument();
    expect(screen.getByText('Unit')).toBeInTheDocument();
    expect(screen.getByText('Glucose')).toBeInTheDocument();
    expect(screen.getByText('2345-7')).toBeInTheDocument();
  });

  it('renders richtext fields via FormattedText (biomarker info)', () => {
    const item = { name: 'X', info: 'Some clinical significance.' } as CatalogItem;
    render(<CatalogItemInfo item={item} total={1} catalogType="biomarker" hideHeader />);
    expect(screen.getByText(/Some clinical significance/i)).toBeInTheDocument();
  });

  it('collapses Additional fields by default and expands on click', () => {
    const item = {
      name: 'X',
      leftover_field: 'surprise',
    } as CatalogItem;
    render(<CatalogItemInfo item={item} total={1} catalogType="biomarker" hideHeader />);
    // Additional section header present
    const header = screen.getByText(/Additional fields/i).closest('button')!;
    expect(header).toHaveAttribute('aria-expanded', 'false');
    // content hidden initially
    expect(screen.queryByText('surprise')).not.toBeInTheDocument();
    fireEvent.click(header);
    expect(screen.getByText('surprise')).toBeInTheDocument();
  });

  it('renders the "Updated" footer when updated_at is present', () => {
    const item = {
      name: 'C',
      slug: 'c',
      updated_at: new Date(Date.now() - 1000 * 60 * 60 * 24).toISOString(),
    } as unknown as CatalogItem;
    render(<CatalogItemInfo item={item} total={1} catalogType="concept" hideHeader />);
    expect(screen.getByText(/Updated/i)).toBeInTheDocument();
  });

  it('omits the footer when updated_at is absent', () => {
    const item = { name: 'X', slug: 'x' } as CatalogItem;
    const { container } = render(
      <CatalogItemInfo item={item} total={1} catalogType="biomarker" hideHeader />,
    );
    expect(container.textContent).not.toMatch(/Updated/i);
  });

  it('shows a type icon in the empty state', () => {
    render(<CatalogItemInfo item={null} total={0} catalogType="biomarker" hideHeader />);
    expect(document.querySelector('svg')).toBeInTheDocument();
  });

  it('shows the related-summary chip and jumps to relations on click', () => {
    const onJump = vi.fn();
    const item = { name: 'X', slug: 'x', code: '2345-7', preferred_unit_symbol: 'mg/dL', relation_count: 3 } as CatalogItem;
    render(
      <CatalogItemInfo
        item={item}
        total={1}
        catalogType="biomarker"
        onJumpRelations={onJump}
        hideHeader
      />,
    );
    const chip = screen.getByText(/3 relations/i);
    expect(chip).toBeInTheDocument();
    fireEvent.click(chip);
    expect(onJump).toHaveBeenCalledTimes(1);
  });

  it('uses singular "relation" when count is 1', () => {
    const item = { name: 'X', slug: 'x', code: 'c', preferred_unit_symbol: 'u', relation_count: 1 } as CatalogItem;
    render(<CatalogItemInfo item={item} total={1} catalogType="biomarker" onJumpRelations={() => undefined} hideHeader />);
    expect(screen.getByText(/1 relation/i)).toBeInTheDocument();
  });

  it('shows a "Needs attention" badge when a critical field is missing', () => {
    // biomarker missing code + unit
    const item = { name: 'X', slug: 'x' } as CatalogItem;
    render(<CatalogItemInfo item={item} total={1} catalogType="biomarker" hideHeader />);
    expect(screen.getByText(/Needs attention/i)).toBeInTheDocument();
  });

  it('does not show the badge when the item is complete', () => {
    const item = { name: 'X', slug: 'x', code: '2345-7', preferred_unit_symbol: 'mg/dL' } as CatalogItem;
    render(<CatalogItemInfo item={item} total={1} catalogType="biomarker" hideHeader />);
    expect(screen.queryByText(/Needs attention/i)).not.toBeInTheDocument();
  });

  it('renders a Copy JSON affordance', () => {
    const item = { name: 'X', slug: 'x', code: '2345-7', preferred_unit_symbol: 'mg/dL' } as CatalogItem;
    render(<CatalogItemInfo item={item} total={1} catalogType="biomarker" hideHeader />);
    expect(screen.getByRole('button', { name: /Copy JSON/i })).toBeInTheDocument();
  });

  it('filters fields by the in-preview search box (label match)', () => {
    const item = {
      name: 'Glucose',
      slug: 'glucose',
      code: '2345-7',
      coding_system: 'loinc',
      aliases: ['FBS'],
      info: 'Blood sugar level.',
      preferred_unit_symbol: 'mg/dL',
    } as CatalogItem;
    render(<CatalogItemInfo item={item} total={1} catalogType="biomarker" hideHeader />);
    fireEvent.change(screen.getByPlaceholderText(/Filter fields/i), {
      target: { value: 'slug' },
    });
    // "Slug" label matches; "Glucose" name row is filtered out.
    expect(screen.getByText('glucose')).toBeInTheDocument();
    expect(screen.queryByText('Glucose')).not.toBeInTheDocument();
  });

  it('filters fields by value content', () => {
    const item = {
      name: 'Glucose',
      slug: 'glucose',
      code: '2345-7',
      coding_system: 'loinc',
      aliases: ['Fasting Blood Sugar'],
      info: 'info',
      preferred_unit_symbol: 'mg/dL',
    } as CatalogItem;
    render(<CatalogItemInfo item={item} total={1} catalogType="biomarker" hideHeader />);
    fireEvent.change(screen.getByPlaceholderText(/Filter fields/i), {
      target: { value: 'fasting' },
    });
    // only the aliases row (value "Fasting Blood Sugar") survives
    expect(screen.getByText('Fasting Blood Sugar')).toBeInTheDocument();
    expect(screen.queryByText('Glucose')).not.toBeInTheDocument();
  });

  it('clearing the filter restores all fields', () => {
    const item = { name: 'Glucose', slug: 'glucose', code: 'c', coding_system: 'loinc', info: 'i', preferred_unit_symbol: 'u' } as CatalogItem;
    render(<CatalogItemInfo item={item} total={1} catalogType="biomarker" hideHeader />);
    const input = screen.getByPlaceholderText(/Filter fields/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'slug' } });
    expect(screen.queryByText('Glucose')).not.toBeInTheDocument();
    fireEvent.change(input, { target: { value: '' } });
    expect(screen.getByText('Glucose')).toBeInTheDocument();
  });
});
