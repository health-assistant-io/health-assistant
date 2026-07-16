/**
 * completeness — data-quality check for a catalog item.
 *
 * Flags clearly-missing critical fields so the Info tab can surface a "needs
 * attention" badge. Conservative: only fields with an unambiguous "should be
 * set" meaning are checked (codes on coded catalogs, category on allergies,
 * unit on biomarkers). Types without a hard requirement (medication, anatomy,
 * concept) are always considered complete.
 */
import type { CatalogItem, CatalogType } from '../../../types/catalog';

export interface CompletenessResult {
  complete: boolean;
  /** Missing field keys (machine names; the UI maps them to labels). */
  missing: string[];
}

function hasValue(item: CatalogItem, key: string): boolean {
  const v = (item as Record<string, unknown>)[key];
  if (v === null || v === undefined) return false;
  if (typeof v === 'string') return v.trim() !== '';
  if (Array.isArray(v)) return v.length > 0;
  return true;
}

export function getFieldCompleteness(
  type: CatalogType | undefined,
  item: CatalogItem | null,
): CompletenessResult {
  if (!item) return { complete: true, missing: [] };
  const missing: string[] = [];
  switch (type) {
    case 'biomarker':
      if (!hasValue(item, 'code')) missing.push('code');
      // A biomarker is unitless only when it has neither a preferred unit nor a symbol.
      if (!hasValue(item, 'preferred_unit_id') && !hasValue(item, 'preferred_unit_symbol')) {
        missing.push('unit');
      }
      break;
    case 'allergy':
      if (!hasValue(item, 'category')) missing.push('category');
      break;
    case 'vaccine':
      if (!hasValue(item, 'code')) missing.push('code');
      break;
    default:
      break; // medication / anatomy / concept: no hard requirements
  }
  return { complete: missing.length === 0, missing };
}
