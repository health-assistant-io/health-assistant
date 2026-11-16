import { type ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAccessStore } from '../../store/slices/accessSlice';

/** Rendered while the `/me/access` mirror is still hydrating — guards must
 * not flash a no-access state for a permission the user may well hold. */
function GuardChecking() {
  return (
    <div className="flex items-center justify-center min-h-[50vh]">
      <div
        className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"
        role="status"
        aria-label="Checking access…"
      />
    </div>
  );
}

interface RequirePermissionProps {
  /** One permission, or any-of for entries shared by several roles. The
   * server enforces the real permission+scope centrally — this guard only
   * turns a guaranteed 403 into a clear no-access state (ADR 0054). */
  permission: string | string[];
  children: ReactNode;
}

/**
 * UX-side route guard: renders `children` only when the hydrated access
 * mirror holds the permission; otherwise redirects to `/no-access`
 * (never a blank page, never a retry loop). Fail closed — an unhydrated
 * or failed mirror waits, then denies.
 */
export function RequirePermission({ permission, children }: RequirePermissionProps) {
  const isHydrated = useAccessStore((s) => s.isHydrated);
  const location = useLocation();
  const allowed = useAccessStore((s) =>
    Array.isArray(permission) ? s.canAny(...permission) : s.can(permission),
  );

  if (!isHydrated) {
    return <GuardChecking />;
  }
  if (!allowed) {
    return <Navigate to="/no-access" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}
