import { useAccessStore } from '../store/slices/accessSlice';

/**
 * Read-side helpers over the `/me/access` mirror (ADR 0054). All of them
 * fail closed: before the first successful hydration (and after any fetch
 * failure) every answer is `false`, so guards and nav render the no-access
 * state instead of flashing content the server would refuse anyway.
 */

export function useAccess() {
  return useAccessStore();
}

/** One permission — `can('user:read')`. */
export function useCan(permission: string): boolean {
  return useAccessStore((s) => s.can(permission));
}

/** Any of the listed permissions (nav entries shared by several roles). */
export function useCanAny(...permissions: string[]): boolean {
  return useAccessStore((s) => s.canAny(...permissions));
}
