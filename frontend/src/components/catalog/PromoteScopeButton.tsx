/**
 * PromoteScopeButton — the generic catalog scope-transition control.
 *
 * Renders a dropdown of the scope tiers the current user is allowed to
 * transition this item to (mirrors `CatalogAccessPolicy.check_promote` on the
 * backend). On select it calls `promoteCatalogItem`; on a 409 slug collision
 * it surfaces the conflicting item's name inline.
 *
 * Permission matrix:
 * - SYSTEM_ADMIN: any transition (→ system / → tenant / → user).
 * - ADMIN / MANAGER: user ↔ tenant only.
 * - USER: nothing (button hidden).
 *
 * Used in the catalog workspace detail header next to the ScopeBadge. Works
 * for every registered catalog type (biomarker / medication / allergy /
 * anatomy / vaccine) — no per-type specialization.
 */
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ArrowUpCircle, AlertTriangle } from 'lucide-react';
import type { CatalogScope } from '../../types/catalog';
import { promoteCatalogItem } from '../../services/catalogService';
import { useAuthStore, type UserRole } from '../../store/slices/authSlice';

interface PromoteOption {
  scope: CatalogScope;
  labelKey: string;
  defaultLabel: string;
}

const ALL_OPTIONS: PromoteOption[] = [
  { scope: 'system', labelKey: 'catalogs.promote.to_system', defaultLabel: 'Promote to system' },
  { scope: 'tenant', labelKey: 'catalogs.promote.to_tenant', defaultLabel: 'Move to tenant' },
  { scope: 'user', labelKey: 'catalogs.promote.to_user', defaultLabel: 'Move to user scope' },
];

/** The transitions a role is allowed for a given current scope. Mirrors
 *  CatalogAccessPolicy.check_promote on the backend (single source of truth). */
function availableTransitions(
  role: UserRole | undefined,
  currentScope: CatalogScope | undefined,
): PromoteOption[] {
  if (!role || role === 'USER') return [];
  return ALL_OPTIONS.filter((o) => {
    if (o.scope === currentScope) return false; // no same-scope
    if (role === 'SYSTEM_ADMIN') return true;
    // ADMIN / MANAGER: user↔tenant only (no system involvement).
    if (o.scope === 'system') return false;
    if (currentScope === 'system') return false;
    return true;
  });
}

interface PromoteScopeButtonProps {
  catalogType: string;
  itemId: string;
  currentScope: CatalogScope | undefined;
  onPromoted: (updated: Record<string, unknown>) => void;
}

export const PromoteScopeButton: React.FC<PromoteScopeButtonProps> = ({
  catalogType,
  itemId,
  currentScope,
  onPromoted,
}) => {
  const { t } = useTranslation();
  const role = useAuthStore((s) => s.user?.role);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const options = availableTransitions(role, currentScope);
  if (options.length === 0) return null;

  const handleSelect = async (target: CatalogScope) => {
    setOpen(false);
    setError(null);
    setBusy(true);
    try {
      const updated = await promoteCatalogItem(catalogType, itemId, target);
      onPromoted(updated);
    } catch (e: any) {
      const status = e?.response?.status;
      if (status === 409) {
        const body = e.response.data || {};
        const name = body.existing_name ?? body.slug ?? 'an item';
        setError(
          t('catalogs.promote.conflict', {
            defaultValue: 'A {{scope}} item with this slug already exists ({{name}}). Rename or open the existing one.',
            scope: body.target_scope ?? target,
            name,
          }),
        );
      } else {
        const detail = e?.response?.data?.detail || e?.message || t('catalogs.promote.error_generic', { defaultValue: 'Scope change failed.' });
        setError(typeof detail === 'string' ? detail : JSON.stringify(detail));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={busy}
        title={t('catalogs.promote.title', { defaultValue: 'Change scope tier' })}
        className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-semibold rounded-lg border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-dark-muted hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
      >
        <ArrowUpCircle className="w-3.5 h-3.5" />
        <span>{t('catalogs.promote.button', { defaultValue: 'Scope' })}</span>
        <ChevronDown className="w-3 h-3" />
      </button>

      {open && (
        <>
          <div
            className="fixed inset-0 z-10"
            onClick={() => setOpen(false)}
          />
          <div className="absolute right-0 mt-1 w-56 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg z-20 py-1">
            {options.map((o) => (
              <button
                key={o.scope}
                type="button"
                onClick={() => handleSelect(o.scope)}
                disabled={busy}
                className="w-full text-left px-3 py-2 text-xs font-medium text-gray-700 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
              >
                {t(o.labelKey, { defaultValue: o.defaultLabel })}
              </button>
            ))}
          </div>
        </>
      )}

      {error && (
        <div className="absolute right-0 top-full mt-1 w-72 max-w-[80vw] rounded-lg border border-rose-200 dark:border-rose-500/30 bg-rose-50 dark:bg-rose-900/10 p-2.5 text-[11px] text-rose-700 dark:text-rose-300 z-20 flex items-start gap-2">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
          <span className="break-words">{error}</span>
        </div>
      )}
    </div>
  );
};

export default PromoteScopeButton;
