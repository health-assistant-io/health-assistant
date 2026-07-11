/**
 * Shared resolver for a catalog item's dedicated "domain" page (the main-app
 * view for that entity, distinct from the `/catalogs` meta-layer).
 *
 * Extracted from `CatalogWorkspace.domainRoute` so both the workspace and the
 * graph node detail card compute domain links consistently. Returns `null`
 * when the catalog type has no dedicated page (allergy / vaccine / concept) —
 * callers must hide the "Open in domain" affordance in that case.
 *
 * Anatomy is special: its route is `:slug` (not `:id`), so a `slug` is
 * preferred when available. When only the id is known (e.g. a graph node
 * before its lazy detail resolves), the id is used as a best-effort path
 * segment to preserve the workspace's prior behavior.
 */

export function domainRouteForType(
  type: string,
  id: string,
  slug?: string,
): string | null {
  switch ((type || '').toLowerCase()) {
    case 'biomarker':
      return `/biomarkers/details/${id}`;
    case 'medication':
      return `/medications/details/${id}`;
    case 'anatomy':
      return `/anatomy/${slug || id}`;
    default:
      return null;
  }
}
