import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AxiosError } from 'axios';

// The toast surface is what the interceptor drives — stub react-toastify and
// the i18next singleton (see accessDeniedToast.test.ts for the same idiom).
const toastError = vi.fn((_message?: string) => undefined);
vi.mock('react-toastify', () => ({
  toast: { error: (message?: string) => toastError(message) },
}));

vi.mock('i18next', () => ({
  default: {
    isInitialized: true,
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue ?? key,
  },
}));

// The interceptor consults the offline queue only for offline/network paths —
// stub it so the module import stays cheap and side-effect free.
vi.mock('../services/offlineService', () => ({
  offlineService: { addToQueue: vi.fn() },
}));

import api from './axios';
import { resetAccessDeniedThrottle } from './accessDeniedToast';

type AdapterMode = 'gate' | 'row-level';

let mode: AdapterMode = 'gate';

/** Adapter that answers every request with a 403 — with the gate marker
 * (`X-Authz-Denied: 1`) or without it (a row-level denial), per `mode`. */
api.defaults.adapter = async (config) => {
  const headers: Record<string, string> =
    mode === 'gate' ? { 'x-authz-denied': '1' } : {};
  throw new AxiosError(
    'Request failed with status code 403',
    AxiosError.ERR_BAD_REQUEST,
    config,
    {},
    {
      data: { detail: 'Access denied' },
      status: 403,
      statusText: 'Forbidden',
      headers,
      config,
    },
  );
};

describe('axios response interceptor — 403 surfacing (ADR 0054)', () => {
  beforeEach(() => {
    toastError.mockClear();
    resetAccessDeniedThrottle();
    mode = 'gate';
  });

  it('toasts a gate denial (the X-Authz-Denied marker is present)', async () => {
    mode = 'gate';
    await expect(api.get('/some-gated-route')).rejects.toBeTruthy();
    expect(toastError).toHaveBeenCalledTimes(1);
    expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/access denied/i));
  });

  it('does NOT toast a row-level denial (no marker — the caller handles it)', async () => {
    mode = 'row-level';
    await expect(api.get('/some-row-level-route')).rejects.toBeTruthy();
    expect(toastError).not.toHaveBeenCalled();
  });
});
