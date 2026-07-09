/**
 * Per-type custom-tab registry for the unified Catalog workspace.
 *
 * Adding a tab = one component + one entry in `CATALOG_TABS`. The workspace
 * reads this registry and renders every tab whose `types` includes the active
 * catalog type, alongside the built-in Info + Relations tabs.
 *
 * The earlier "Ranges" (biomarker), "Atlas" (anatomy) and "Interactions"
 * (medication) starters were removed in the catalogs UI rework — the layered
 * shell's Info/Relations tabs + the dedicated domain detail pages now cover
 * those needs. The registry is retained so future feature tabs slot in here.
 */
import type { ComponentType } from 'react';
import type { CatalogType, CatalogTypeMeta } from '../../../types/catalog';

export interface CatalogTabProps {
  typeMeta: CatalogTypeMeta;
}

export interface CatalogTabConfig {
  id: string;
  labelKey: string;
  labelFallback: string;
  /** Catalog types this tab appears on. */
  types: CatalogType[];
  Component: ComponentType<CatalogTabProps>;
}

/** The registry — append here to add a custom tab. Order is preserved. */
export const CATALOG_TABS: CatalogTabConfig[] = [];

/** Return the custom tabs applicable to a given catalog type. */
export function getCustomTabs(type: string): CatalogTabConfig[] {
  return CATALOG_TABS.filter((tab) =>
    tab.types.includes(type as CatalogType),
  );
}
