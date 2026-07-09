/**
 * Scope badge — the ownership/scope tier indicator for a catalog item (Phase A
 * access-control model). Colored per the plan §3.2:
 *
 * - 🟣 system (violet) — canonical, SYSTEM_ADMIN only
 * - 🔵 tenant (blue)   — shared across the tenant
 * - 🟢 yours (green)   — a user-scope item created by the current user
 * - 🟡 user   (amber)  — a user-scope item created by someone else
 *
 * The "yours" vs "user" distinction needs the current user id: a user-scope
 * item is "yours" when `created_by === currentUserId`.
 */
import React from 'react';
import type { CatalogScope } from '../../types/catalog';

type BadgeVariant = 'system' | 'tenant' | 'yours' | 'user';

const VARIANT_CLASSES: Record<BadgeVariant, string> = {
  system:
    'bg-violet-50 dark:bg-violet-900/20 text-violet-600 dark:text-violet-400 border-violet-100 dark:border-violet-800/30',
  tenant:
    'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border-blue-100 dark:border-blue-800/30',
  yours:
    'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-600 dark:text-emerald-400 border-emerald-100 dark:border-emerald-800/30',
  user:
    'bg-amber-50 dark:bg-amber-900/20 text-amber-600 dark:text-amber-400 border-amber-100 dark:border-amber-800/30',
};

const VARIANT_LABEL: Record<BadgeVariant, string> = {
  system: 'System',
  tenant: 'Tenant',
  yours: 'Yours',
  user: 'User',
};

export function scopeVariant(
  scope: CatalogScope | undefined,
  created_by: string | null | undefined,
  currentUserId: string | null | undefined,
): BadgeVariant {
  if (scope === 'system') return 'system';
  if (scope === 'tenant') return 'tenant';
  // user scope — "yours" if the current user is the creator, else someone else's.
  if (created_by && currentUserId && created_by === currentUserId) return 'yours';
  return 'user';
}

interface ScopeBadgeProps {
  scope?: CatalogScope;
  created_by?: string | null;
  currentUserId?: string | null;
  /** When provided, the badge renders as a button that drives a filter. */
  onClick?: () => void;
  /** Highlights the badge when its filter is the active one. */
  active?: boolean;
}

export const ScopeBadge: React.FC<ScopeBadgeProps> = ({
  scope,
  created_by,
  currentUserId,
  onClick,
  active = false,
}) => {
  const variant = scopeVariant(scope, created_by ?? null, currentUserId ?? null);
  const base = `inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border ${VARIANT_CLASSES[variant]} ${
    onClick ? 'cursor-pointer transition-transform hover:scale-105' : ''
  }`;
  const title = `Scope: ${scope ?? 'system'}${onClick ? ' — click to filter' : ''}`;
  const style = active ? { boxShadow: '0 0 0 2px rgba(59,130,246,0.55)' } : undefined;
  if (onClick) {
    return (
      <button type="button" onClick={onClick} className={base} title={title} style={style}>
        {VARIANT_LABEL[variant]}
      </button>
    );
  }
  return (
    <span className={base} title={title} style={style}>
      {VARIANT_LABEL[variant]}
    </span>
  );
};
