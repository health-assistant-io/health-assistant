import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, dftOrOpts?: any) => {
      let s: string;
      let opts: Record<string, unknown>;
      if (typeof dftOrOpts === 'string') {
        s = dftOrOpts;
        opts = {};
      } else {
        s = dftOrOpts?.defaultValue ?? k;
        opts = dftOrOpts ?? {};
      }
      for (const [key, val] of Object.entries(opts)) {
        if (key === 'defaultValue') continue;
        s = s.replace(new RegExp(`{{\\s*${key}\\s*}}`, 'g'), String(val));
      }
      return s;
    },
    i18n: { language: 'en' },
  }),
}));

import { InstancePreview } from '../InstancePreview';
import type { InstanceRow } from '../types';

const row: InstanceRow = {
  id: 'e1',
  type: 'examination',
  label: 'Blood Test',
  subtitle: 'Visit note',
  description: '# Heading\n\nSome **markdown** body.',
  date: '2026-01-02T00:00:00Z',
  status: 'completed',
  icon: 'Stethoscope',
  badges: [{ label: 'Lab' }],
  raw: {},
};

describe('InstancePreview', () => {
  it('renders the empty state when no row is given', () => {
    render(<InstancePreview row={null} />);
    expect(screen.getByText('Select a record to preview')).toBeInTheDocument();
  });

  it('renders the label, type chip, status, and a badge', () => {
    render(<InstancePreview row={row} />);
    expect(screen.getByText('Blood Test')).toBeInTheDocument();
    expect(screen.getByText('examination')).toBeInTheDocument();
    expect(screen.getByText('completed')).toBeInTheDocument();
    expect(screen.getByText('Lab')).toBeInTheDocument();
  });

  it('renders the rich description via FormattedText (markdown heading)', () => {
    const { container } = render(<InstancePreview row={row} />);
    // FormattedText renders markdown as an <h1> for "# Heading".
    expect(container.querySelector('h1')).toBeTruthy();
    expect(container.querySelector('h1')?.textContent).toContain('Heading');
  });
});
