import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, opts?: any) => opts?.defaultValue ?? k }),
}));

const mockToastSuccess = vi.fn();
const mockToastError = vi.fn();
vi.mock('react-toastify', () => ({
  toast: { success: (...a: unknown[]) => mockToastSuccess(...a), error: (...a: unknown[]) => mockToastError(...a) },
}));

import { CopyButton } from '../CopyButton';

function stubClipboard(writeText: ReturnType<typeof vi.fn> = vi.fn().mockResolvedValue(undefined)) {
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
    writable: true,
  });
  return writeText;
}

describe('CopyButton', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders nothing when value is empty and hideWhenEmpty is true', () => {
    stubClipboard();
    const { container } = render(<CopyButton value="" />);
    expect(container.firstChild).toBeNull();
  });

  it('renders when value is empty and hideWhenEmpty is false', () => {
    stubClipboard();
    render(<CopyButton value="" hideWhenEmpty={false} />);
    expect(screen.getByRole('button')).toBeInTheDocument();
  });

  it('copies the value to the clipboard and shows a success toast', async () => {
    const writeText = stubClipboard();
    render(<CopyButton value="abc-123" />);
    await fireEvent.click(screen.getByRole('button'));
    expect(writeText).toHaveBeenCalledWith('abc-123');
    await waitFor(() => expect(mockToastSuccess).toHaveBeenCalled());
  });

  it('shows an error toast when the clipboard rejects', async () => {
    stubClipboard(vi.fn().mockRejectedValue(new Error('denied')));
    render(<CopyButton value="abc-123" />);
    await fireEvent.click(screen.getByRole('button'));
    await waitFor(() => expect(mockToastError).toHaveBeenCalled());
  });

  it('stops event propagation so it does not trigger a parent click', async () => {
    stubClipboard();
    const parentClick = vi.fn();
    render(
      <div onClick={parentClick}>
        <CopyButton value="abc-123" />
      </div>,
    );
    await fireEvent.click(screen.getByRole('button'));
    expect(parentClick).not.toHaveBeenCalled();
  });
});
