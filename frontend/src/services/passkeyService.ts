/**
 * WebAuthn / passkey ceremonies (program 12C2).
 *
 * The server owns the challenge and verification; this module only runs
 * the browser ceremony and posts the resulting `PublicKeyCredential` JSON
 * back. Every ceremony requires user verification (enforced server-side).
 */
import {
  browserSupportsWebAuthn,
  startAuthentication,
  startRegistration,
  type PublicKeyCredentialCreationOptionsJSON,
  type PublicKeyCredentialRequestOptionsJSON,
} from '@simplewebauthn/browser';
import api from '../api/axios';

export interface Passkey {
  id: string;
  name: string;
  transports: string[];
  aaguid: string | null;
  backup_eligible: boolean;
  backup_state: boolean;
  created_at: string | null;
  last_used_at: string | null;
  disabled: boolean;
}

interface OptionsEnvelope<T> {
  options: T;
  expires_in: number;
}

function webauthnAvailable(): boolean {
  try {
    return typeof window !== 'undefined' && browserSupportsWebAuthn();
  } catch {
    return false;
  }
}

/** Whether this browser can run a WebAuthn ceremony. */
export function passkeysSupported(): boolean {
  return webauthnAvailable();
}

export async function listMyPasskeys(): Promise<Passkey[]> {
  const response = await api.get<{ items: Passkey[] }>('/me/mfa/passkeys');
  return Array.isArray(response.data?.items) ? response.data.items : [];
}

/** Register a new credential; the name is the user-facing label. */
export async function registerPasskey(name: string): Promise<Passkey> {
  const options = await api.post<OptionsEnvelope<PublicKeyCredentialCreationOptionsJSON>>(
    '/me/mfa/passkeys/options',
  );
  const credential = await startRegistration({ optionsJSON: options.data.options });
  const response = await api.post<Passkey>('/me/mfa/passkeys/verify', {
    credential,
    name,
  });
  return response.data;
}

export async function deleteMyPasskey(credentialId: string): Promise<void> {
  await api.delete(`/me/mfa/passkeys/${credentialId}`);
}

/** Passkey sign-in (optionally named by email; omit for discoverable). */
export async function loginWithPasskey(email?: string): Promise<void> {
  const options = await api.post<OptionsEnvelope<PublicKeyCredentialRequestOptionsJSON>>(
    '/auth/passkeys/options',
    email ? { email } : {},
  );
  const credential = await startAuthentication({ optionsJSON: options.data.options });
  await api.post('/auth/passkeys/verify', { credential });
}

/** Prove one step-up action with a passkey assertion. */
export async function stepUpWithPasskey(action: string): Promise<void> {
  const options = await api.post<
    OptionsEnvelope<PublicKeyCredentialRequestOptionsJSON> & { action: string }
  >('/auth/step-up/passkeys/options', { action });
  const credential = await startAuthentication({ optionsJSON: options.data.options });
  await api.post('/auth/step-up/passkeys/verify', { action, credential });
}
