import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MFAEnrollmentPanel } from '../MFAEnrollmentPanel';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, fallback?: string) => fallback ?? key }),
}));

const ENROLLMENT = {
  secret: 'JBSWY3DPEHPK3PXP',
  uri: 'otpauth://totp/Health:you@example.com?secret=JBSWY3DPEHPK3PXP',
  recovery_codes: ['aaaa-bbbb', 'cccc-dddd'],
};

const writeText = vi.fn<(value: string) => Promise<void>>();

beforeEach(() => {
  writeText.mockReset();
  writeText.mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  });
});

describe('MFAEnrollmentPanel copy actions', () => {
  it('exposes every copy affordance through the shared copy button', () => {
    render(<MFAEnrollmentPanel enrollment={ENROLLMENT} />);

    expect(screen.getByRole('button', { name: 'Copy' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy URI' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy all' })).toBeInTheDocument();
  });

  it('copies the manual setup key and latches the copied state', async () => {
    render(<MFAEnrollmentPanel enrollment={ENROLLMENT} />);

    const button = screen.getByRole('button', { name: 'Copy' });
    fireEvent.click(button);

    expect(writeText).toHaveBeenCalledWith(ENROLLMENT.secret);
    await waitFor(() => expect(button).toHaveAttribute('data-copied'));
  });

  it('copies all recovery codes one per line', () => {
    render(<MFAEnrollmentPanel enrollment={ENROLLMENT} />);

    fireEvent.click(screen.getByRole('button', { name: 'Copy all' }));

    expect(writeText).toHaveBeenCalledWith(ENROLLMENT.recovery_codes.join('\n'));
  });
});
