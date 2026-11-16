/**
 * Step-up re-authentication API (program 12C1).
 *
 * `POST /auth/step-up` proves presence with the account's own password
 * and/or TOTP/recovery code; a passkey proves the same action through the
 * WebAuthn assertion ceremony. The proof is a ten-minute window bound to
 * the sign-in family and one action — the caller retries the gated request
 * once it succeeds.
 */
import api from '../api/axios';
import {
  STEP_UP_ACTIONS,
  isStepUpAction,
  stepUpChallengeOf,
  type StepUpAction,
  type StepUpChallenge,
  type StepUpProof,
} from './stepUpTypes';

export { STEP_UP_ACTIONS, isStepUpAction, stepUpChallengeOf };
export type { StepUpAction, StepUpChallenge, StepUpProof };

export interface StepUpCredentials {
  password?: string;
  code?: string;
}

export async function requestStepUp(
  action: StepUpAction | string,
  credentials: StepUpCredentials = {},
): Promise<StepUpProof> {
  const body: Record<string, string> = { action };
  if (credentials.password) body.password = credentials.password;
  if (credentials.code) body.code = credentials.code;
  const response = await api.post<StepUpProof>('/auth/step-up', body);
  return response.data;
}
