import { describe, expect, it } from 'vitest';
import { isStepUpAction, stepUpChallengeOf } from './stepUpTypes';

function forbidden(detail: unknown) {
  return { response: { status: 403, data: { detail } } };
}

describe('stepUpChallengeOf', () => {
  it('parses a step_up_required denial with its available methods', () => {
    const challenge = stepUpChallengeOf(
      forbidden({
        code: 'step_up_required',
        action: 'break_glass',
        expires_in: 600,
        methods: ['password', 'totp', 'webauthn'],
      }),
    );
    expect(challenge).toEqual({
      code: 'step_up_required',
      action: 'break_glass',
      expires_in: 600,
      methods: ['password', 'totp', 'webauthn'],
      reason: undefined,
    });
  });

  it('parses an unavailable denial with its reason', () => {
    const challenge = stepUpChallengeOf(
      forbidden({ code: 'step_up_unavailable', action: 'export', reason: 'no_factor' }),
    );
    expect(challenge?.code).toBe('step_up_unavailable');
    expect(challenge?.reason).toBe('no_factor');
    expect(challenge?.expires_in).toBe(600);
    expect(challenge?.methods).toEqual([]);
  });

  it('ignores ordinary 403s, other statuses and malformed bodies', () => {
    expect(stepUpChallengeOf(forbidden('Access denied'))).toBeNull();
    expect(stepUpChallengeOf(forbidden({ code: 'other' }))).toBeNull();
    expect(stepUpChallengeOf({ response: { status: 401, data: { detail: 'nope' } } })).toBeNull();
    expect(stepUpChallengeOf(new Error('network'))).toBeNull();
  });

  it('keeps only string method values', () => {
    const challenge = stepUpChallengeOf(
      forbidden({ code: 'step_up_required', action: 'export', methods: ['password', 42, null] }),
    );
    expect(challenge?.methods).toEqual(['password']);
  });

  it('recognises the closed action set', () => {
    expect(isStepUpAction('role_change')).toBe(true);
    expect(isStepUpAction('mfa_change')).toBe(true);
    expect(isStepUpAction('delete_everything')).toBe(false);
  });
});
