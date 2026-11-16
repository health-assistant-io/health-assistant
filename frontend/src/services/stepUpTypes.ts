/**
 * Step-up challenge contract (ADR 0054 Phase 6 / program 12C1).
 *
 * Kept dependency-free so the axios interceptor can parse a denial without
 * importing the API client (which would be a cycle). The wire shape is
 * `403 {detail: {code, action, expires_in, methods?, reason?}}`.
 */

export type StepUpAction = 'role_change' | 'export' | 'break_glass' | 'grant_admin' | 'mfa_change';

export const STEP_UP_ACTIONS: readonly StepUpAction[] = [
  'role_change',
  'export',
  'break_glass',
  'grant_admin',
  'mfa_change',
];

export type StepUpMethod = 'password' | 'totp' | 'webauthn' | 'federated' | 'recovery';

export interface StepUpChallenge {
  code: 'step_up_required' | 'step_up_unavailable';
  action: string;
  expires_in: number;
  methods: StepUpMethod[];
  reason?: string;
}

export interface StepUpProof {
  action: string;
  methods: string[];
  issued_at: number;
  expires_at: number;
  expires_in: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object';
}

/** The typed step-up denial carried by a 403, or null for any other error. */
export function stepUpChallengeOf(error: unknown): StepUpChallenge | null {
  const response = (error as { response?: { status?: number; data?: unknown } })?.response;
  if (response?.status !== 403 || !isRecord(response.data)) return null;
  const detail = response.data.detail;
  if (!isRecord(detail)) return null;
  const code = detail.code;
  if (code !== 'step_up_required' && code !== 'step_up_unavailable') return null;
  const action = typeof detail.action === 'string' ? detail.action : '';
  const rawMethods = Array.isArray(detail.methods) ? detail.methods : [];
  return {
    code,
    action,
    expires_in: typeof detail.expires_in === 'number' ? detail.expires_in : 600,
    methods: rawMethods.filter((method): method is StepUpMethod =>
      typeof method === 'string',
    ),
    reason: typeof detail.reason === 'string' ? detail.reason : undefined,
  };
}

export function isStepUpAction(value: string): value is StepUpAction {
  return (STEP_UP_ACTIONS as readonly string[]).includes(value);
}
