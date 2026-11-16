import { beforeEach, describe, expect, it, vi } from 'vitest';

// react-toastify and i18n are heavy app singletons — stub the surface the
// module uses so the throttle logic is what is under test.
const toastError = vi.fn((_message?: string) => undefined);
vi.mock('react-toastify', () => ({
  toast: { error: (message?: string) => toastError(message) },
}));

const tSpy = vi.fn((key: string, options?: { defaultValue?: string }) => options?.defaultValue ?? key);
vi.mock('i18next', () => ({
  default: {
    isInitialized: true,
    t: (key: string, options?: { defaultValue?: string }) => tSpy(key, options),
  },
}));

import { notifyAccessDenied, resetAccessDeniedThrottle } from './accessDeniedToast';

describe('notifyAccessDenied (central 403 surfacing)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetAccessDeniedThrottle();
    toastError.mockClear();
  });

  it('shows one clear toast on a denied call', () => {
    notifyAccessDenied();
    expect(toastError).toHaveBeenCalledTimes(1);
    expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/access denied/i));
  });

  it('throttles a burst of parallel denials to a single toast', () => {
    notifyAccessDenied();
    notifyAccessDenied();
    notifyAccessDenied();
    expect(toastError).toHaveBeenCalledTimes(1);
  });

  it('shows a new toast after the throttle window passes', () => {
    notifyAccessDenied();
    expect(toastError).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(3001);
    notifyAccessDenied();
    expect(toastError).toHaveBeenCalledTimes(2);
  });
});
