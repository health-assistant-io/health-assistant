import { toast } from 'react-toastify';
import i18next from 'i18next';

/**
 * Central gate-denial surfacing (ADR 0054): the central route gate answers
 * "Access denied" with 403 and marks the response with `X-Authz-Denied`;
 * the axios interceptor turns such a response into one clear toast instead
 * of a silent failure. Row-level denials (no marker) are NOT surfaced here.
 * Throttled so a page firing several denied calls in parallel shows a
 * single message, and deliberately non-retrying: a 403 never re-issues the
 * request.
 *
 * The shared i18next singleton is the one `src/i18n.ts` configured at boot
 * (imported for its side effect from `main.tsx`) — deliberately NOT that
 * module, so this file stays importable wherever `react-i18next` is
 * mocked away. Uninitialized (tests): the fallback string stands.
 */

const THROTTLE_MS = 3000;

const FALLBACK_MESSAGE = 'Access denied — your role does not allow this action.';

let lastNotifiedAt = 0;

export function notifyAccessDenied(): void {
  const now = Date.now();
  if (now - lastNotifiedAt < THROTTLE_MS) {
    return;
  }
  lastNotifiedAt = now;
  const message = i18next.isInitialized
    ? i18next.t('no_access.toast', { defaultValue: FALLBACK_MESSAGE })
    : FALLBACK_MESSAGE;
  toast.error(message);
}

/** Test hook: reset the throttle window. */
export function resetAccessDeniedThrottle(): void {
  lastNotifiedAt = 0;
}
