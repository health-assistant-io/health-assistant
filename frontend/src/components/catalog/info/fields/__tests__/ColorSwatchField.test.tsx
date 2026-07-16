import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, opts?: any) => opts?.defaultValue ?? k }),
}));
vi.mock('react-toastify', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { ColorSwatchField } from '../ColorSwatchField';

describe('ColorSwatchField', () => {
  beforeEach(() => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      configurable: true,
      writable: true,
    });
  });

  it('renders a muted dash for empty/non-string values', () => {
    const { rerender } = render(<ColorSwatchField value="" />);
    expect(screen.getByText('—')).toBeInTheDocument();
    rerender(<ColorSwatchField value={null} />);
    expect(screen.getByText('—')).toBeInTheDocument();
    rerender(<ColorSwatchField value={undefined} />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('renders the hex value in mono and a swatch with that background', () => {
    render(<ColorSwatchField value="#3b82f6" />);
    expect(screen.getByText('#3b82f6')).toBeInTheDocument();
    const swatch = screen.getByRole('img', { name: /Color #3b82f6/i });
    expect(swatch).toHaveStyle({ backgroundColor: '#3b82f6' });
  });

  it('trims whitespace around the value', () => {
    render(<ColorSwatchField value="  #ff0000  " />);
    expect(screen.getByText('#ff0000')).toBeInTheDocument();
  });
});
