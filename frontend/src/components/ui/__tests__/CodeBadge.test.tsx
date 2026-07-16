import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, opts?: any) => opts?.defaultValue ?? k }),
}));
vi.mock('react-toastify', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { CodeBadge } from '../CodeBadge';

describe('CodeBadge', () => {
  beforeEach(() => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      configurable: true,
      writable: true,
    });
  });

  it('renders a muted dash when code is empty', () => {
    render(<CodeBadge code="" system="loinc" />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('renders the code in mono and the system label', () => {
    render(<CodeBadge code="2345-7" system="loinc" />);
    expect(screen.getByText('2345-7')).toBeInTheDocument();
    expect(screen.getByText('LOINC')).toBeInTheDocument();
  });

  it('builds a LOINC external link with noopener noreferrer', () => {
    render(<CodeBadge code="2345-7" system="loinc" copyable={false} />);
    const link = screen.getByRole('link');
    expect(link).toHaveAttribute('href', 'https://loinc.org/2345-7');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('resolves SNOMED, CVX, ATC, ICD-10, FMA lookups', () => {
    const cases: Array<[string, string, RegExp]> = [
      ['snomed', '73211009', /browser\.ihtsdotools\.org/],
      ['cvx', '03', /hl7\.org/],
      ['atc', 'A10BA02', /whocc\.no/],
      ['icd10', 'E11', /icd\.who\.int/],
      ['fma', '7088', /bioportal\.bioontology\.org/],
    ];
    for (const [sys, code, hrefRe] of cases) {
      const { unmount } = render(
        <CodeBadge code={code} system={sys} copyable={false} />,
      );
      expect(screen.getByRole('link').getAttribute('href')).toMatch(hrefRe);
      unmount();
    }
  });

  it('renders no external link for custom/unknown system without lookupHref', () => {
    render(<CodeBadge code="X1" system="custom" copyable={false} />);
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    // custom hides the system label
    expect(screen.queryByText('CUSTOM')).not.toBeInTheDocument();
  });

  it('uses an explicit lookupHref function when provided', () => {
    render(
      <CodeBadge
        code="D000001"
        system="mesh"
        lookupHref={(c) => `https://meshb.nlm.nih.gov/record/ui?ui=${c}`}
        copyable={false}
      />,
    );
    expect(screen.getByRole('link')).toHaveAttribute(
      'href',
      'https://meshb.nlm.nih.gov/record/ui?ui=D000001',
    );
  });

  it('renders the accessible label mentioning the system + external', () => {
    render(<CodeBadge code="2345-7" system="loinc" copyable={false} />);
    expect(screen.getByRole('link').getAttribute('aria-label')).toMatch(
      /LOINC 2345-7.*opens external/i,
    );
  });
});
