import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, opts?: any) => opts?.defaultValue ?? k }),
}));
vi.mock('react-toastify', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { KeyValueGrid } from '../KeyValueGrid';
import type { KeyValueEntry } from '../KeyValueGrid';

describe('KeyValueGrid', () => {
  beforeEach(() => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      configurable: true,
      writable: true,
    });
  });

  const entries: KeyValueEntry[] = [
    { key: 'name', label: 'Name', value: 'Glucose' },
    { key: 'code', label: 'Code', value: '2345-7', mono: true, copyable: true },
    { key: 'empty', label: 'Empty', value: '' },
  ];

  it('renders a semantic dl with one row per entry', () => {
    render(<KeyValueGrid entries={entries} />);
    expect(document.querySelector('dl')).toBeInTheDocument();
    expect(screen.getByText('Glucose')).toBeInTheDocument();
    expect(screen.getByText('2345-7')).toBeInTheDocument();
  });

  it('renders a muted dash for empty values', () => {
    render(<KeyValueGrid entries={[{ key: 'e', label: 'E', value: '' }]} />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('applies the mono class to mono values', () => {
    render(<KeyValueGrid entries={[{ key: 'c', label: 'C', value: 'X1', mono: true }]} />);
    expect(screen.getByText('X1').className).toContain('font-mono');
  });

  it('renders a copy button for copyable rows', () => {
    render(
      <KeyValueGrid
        entries={[{ key: 'c', label: 'C', value: 'abc', copyable: true }]}
      />,
    );
    expect(screen.getByRole('button')).toBeInTheDocument();
  });

  it('uses copyValue when provided instead of stringified value', () => {
    render(
      <KeyValueGrid
        entries={[{ key: 'c', label: 'C', value: 'display', copyable: true, copyValue: 'raw-uuid' }]}
      />,
    );
    expect(screen.getByText('display')).toBeInTheDocument();
  });

  it('renders nothing when entries is empty', () => {
    const { container } = render(<KeyValueGrid entries={[]} />);
    expect(container.firstChild).toBeNull();
  });
});
