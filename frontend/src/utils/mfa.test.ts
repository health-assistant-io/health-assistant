import { describe, it, expect } from 'vitest';
import { isMfaChallenge, mfaChallengeOf } from '../services/mfaService';

const challenge = (over: Record<string, unknown> = {}) => ({
  response: {
    status: 401,
    data: { detail: 'mfa_required', mfa_token: 'tok', enrollment_needed: false, ...over },
  },
});

describe('isMfaChallenge (H5 login 401 classifier)', () => {
  it('recognizes the mfa_required challenge body', () => {
    expect(isMfaChallenge(challenge())).toBe(true);
    expect(isMfaChallenge(challenge({ enrollment_needed: true }))).toBe(true);
  });

  it('rejects plain credential 401s — the login page must show the password error', () => {
    expect(isMfaChallenge({ response: { status: 401, data: { detail: 'Invalid email or password' } } })).toBe(false);
    expect(isMfaChallenge({ response: { status: 401 } })).toBe(false);
  });

  it('rejects non-401 statuses and non-axios shapes', () => {
    expect(isMfaChallenge({ response: { status: 423, data: { detail: 'mfa_required', mfa_token: 'x' } } })).toBe(false);
    expect(isMfaChallenge(new Error('Network Error'))).toBe(false);
    expect(isMfaChallenge(null)).toBe(false);
  });

  it('extracts the challenge via mfaChallengeOf', () => {
    const body = mfaChallengeOf(challenge({ mfa_token: 'abc', enrollment_needed: true }));
    expect(body).toEqual({ detail: 'mfa_required', mfa_token: 'abc', enrollment_needed: true });
    expect(mfaChallengeOf({ response: { status: 500 } })).toBeNull();
  });
});
