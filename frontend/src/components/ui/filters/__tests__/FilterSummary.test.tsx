import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, opts?: any) => {
      let s = opts?.defaultValue ?? k;
      if (opts) {
        for (const [key, val] of Object.entries(opts)) {
          if (key === 'defaultValue') continue;
          s = s.replace(new RegExp(`{{\\s*${key}\\s*}}`, 'g'), String(val));
        }
      }
      return s;
    },
  }),
}));

import { FilterSummary } from '../FilterSummary';

describe('FilterSummary', () => {
  it('renders nothing when no active filters and no counts', () => {
    const { container } = render(<FilterSummary activeCount={0} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows active filter count when > 0', () => {
    render(<FilterSummary activeCount={2} />);
    expect(screen.getByText(/2 filter/)).toBeInTheDocument();
  });

  it('shows result count when both counts are provided', () => {
    render(<FilterSummary activeCount={0} resultCount={5} totalCount={20} />);
    expect(screen.getByText('5 of 20')).toBeInTheDocument();
  });

  it('joins active count and result count with a separator', () => {
    render(<FilterSummary activeCount={3} resultCount={5} totalCount={20} />);
    expect(screen.getByText(/3 filter/)).toBeInTheDocument();
    expect(screen.getByText(/5 of 20/)).toBeInTheDocument();
  });
});
