/**
 * Shared catalog-type → leading-icon map (matches the backend registrations'
 * `ui.icon`). The single source of truth so the catalog browser
 * ({@link CatalogItemInfo}), the compact selection card
 * ({@link CatalogItemCard}), and any future surface never drift when a
 * catalog type is added or its icon changes.
 */
import {
  Activity,
  Pill,
  ShieldAlert,
  PersonStanding,
  Syringe,
  Network,
  Inbox,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { CatalogType } from '../../types/catalog';

export const CATALOG_TYPE_ICONS: Record<CatalogType, LucideIcon> = {
  biomarker: Activity,
  medication: Pill,
  allergy: ShieldAlert,
  anatomy: PersonStanding,
  vaccine: Syringe,
  concept: Network,
};

/**
 * Resolve the leading icon for a catalog type. Falls back to a neutral inbox
 * glyph for unknown / dynamically-registered types so callers never render a
 * broken/empty icon — mirrors the defensive `InstanceCard` fallback.
 */
export function getCatalogTypeIcon(type: string | undefined | null): LucideIcon {
  if (!type) return Inbox;
  return (CATALOG_TYPE_ICONS as Record<string, LucideIcon>)[type] ?? Inbox;
}
