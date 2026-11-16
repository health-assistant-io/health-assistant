import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AxiosError } from 'axios';

// The toast surface is what the interceptor drives — stub react-toastify and
// the i18next singleton (same idiom as axios.test.ts).
vi.mock('react-toastify', () => ({
  toast: { error: vi.fn() },
}));

vi.mock('i18next', () => ({
  default: {
    isInitialized: true,
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue ?? key,
  },
}));

vi.mock('../services/offlineService', () => ({
  offlineService: { addToQueue: vi.fn() },
}));

import axios from 'axios';
import { consumeSessionEndReason, refreshSession, storeSessionEndReason } from './axios';

/** Adapter answering POST /auth/refresh with `status` + `detail`; everything
 * else gets a 200 (the 401-refresh funnel is the surface under test). */
let refreshFailure: { status: number; detail: string } | null = null;

function adapter(config: import('axios').InternalAxiosRequestConfig) {
  if (config.url?.includes('/auth/refresh') && refreshFailure) {
    throw new AxiosError(
      'Request failed',
      AxiosError.ERR_BAD_REQUEST,
      config,
      {},
      {
        data: { detail: refreshFailure.detail },
        status: refreshFailure.status,
        statusText: 'Forbidden',
        headers: {},
        config,
      },
    );
  }
  return Promise.resolve({
    data: {},
    status: 200,
    statusText: 'OK',
    headers: {},
    config,
  });
}

axios.defaults.adapter = adapter as import('axios').AxiosAdapter;

describe('session-end reason capture (ADR 0054 phase 3)', () => {
  beforeEach(() => {
    refreshFailure = null;
    sessionStorage.clear();
  });

  it('captures the idle-window denial from a failed refresh', async () => {
    refreshFailure = { status: 401, detail: 'session_idle_expired' };
    await expect(refreshSession()).resolves.toBe(false);
    expect(consumeSessionEndReason()).toBe('session_idle_expired');
    expect(consumeSessionEndReason()).toBeNull();
  });

  it('captures the max-age denial from a failed refresh', async () => {
    refreshFailure = { status: 401, detail: 'session_max_age_expired' };
    await expect(refreshSession()).resolves.toBe(false);
    expect(consumeSessionEndReason()).toBe('session_max_age_expired');
  });

  it('captures the IP allowlist denial (403) from a failed refresh', async () => {
    refreshFailure = { status: 403, detail: 'ip_not_allowed' };
    await expect(refreshSession()).resolves.toBe(false);
    expect(consumeSessionEndReason()).toBe('ip_not_allowed');
  });

  it('stores nothing for an untyped refresh failure', async () => {
    refreshFailure = { status: 401, detail: 'Refresh token has been revoked' };
    await expect(refreshSession()).resolves.toBe(false);
    expect(consumeSessionEndReason()).toBeNull();
  });

  it('round-trips the helper directly and rejects unknown reasons', () => {
    storeSessionEndReason('ip_not_allowed');
    expect(consumeSessionEndReason()).toBe('ip_not_allowed');
    sessionStorage.setItem('nx_session_end', 'nonsense');
    expect(consumeSessionEndReason()).toBeNull();
  });
});
