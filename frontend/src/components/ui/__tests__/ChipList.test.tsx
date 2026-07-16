import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { ChipList } from '../ChipList';

describe('ChipList', () => {
  it('renders each non-empty item as a pill', () => {
    const { container } = render(<ChipList items={['glucose', 'fbs', '']} />);
    expect(screen.getByText('glucose')).toBeInTheDocument();
    expect(screen.getByText('fbs')).toBeInTheDocument();
    // empty string filtered out → only 2 list items rendered
    expect(container.querySelectorAll('li')).toHaveLength(2);
  });

  it('renders the emptyText placeholder when the list is empty', () => {
    render(<ChipList items={[null, '', undefined]} emptyText="None" />);
    expect(screen.getByText('None')).toBeInTheDocument();
  });

  it('renders nothing when empty and no emptyText', () => {
    const { container } = render(<ChipList items={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders static spans (not buttons) when onItemClick is omitted', () => {
    render(<ChipList items={['a']} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.getByText('a').tagName).toBe('SPAN');
  });

  it('renders buttons and fires onItemClick with value + index', () => {
    const onClick = vi.fn();
    render(<ChipList items={['a', 'b']} onItemClick={onClick} />);
    fireEvent.click(screen.getByText('b'));
    expect(onClick).toHaveBeenCalledWith('b', 1);
  });

  it('applies the danger variant classes', () => {
    render(<ChipList items={['peanut']} variant="danger" />);
    const pill = screen.getByText('peanut');
    expect(pill.className).toContain('bg-rose-50');
  });

  it('shows a chevron on clickable chips when showChevron is set', () => {
    render(<ChipList items={['a']} onItemClick={() => undefined} showChevron />);
    // chevron is an svg with aria-hidden; query within the button
    const btn = screen.getByRole('button');
    expect(btn.querySelector('svg')).toBeInTheDocument();
  });
});
