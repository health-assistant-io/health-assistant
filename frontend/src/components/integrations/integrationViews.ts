import {
  EXTENSION_CAPABILITY_TIERS,
  EXTENSION_CATEGORIES,
  EXTENSION_KINDS,
  isExtensionCapabilityId,
  isExtensionStatusState,
  type ExtensionCategory,
  type ExtensionKind,
  type ExtensionStatusState,
  type ExtensionTier,
  type IntegrationCapabilityView,
  type IntegrationCatalogEntryView,
  type IntegrationConditionId,
  type IntegrationDocsNode,
  type IntegrationExtensionGroupView,
  type IntegrationExtensionView,
  type IntegrationInstanceView,
} from '../ui/integrations';
import type {
  ActiveIntegration,
  IntegrationDocsTree,
  IntegrationManifest,
} from '../../services/integrationService';

/**
 * Pure wire → kit view-model mapping (plan 33 Phase D — desktop's
 * integration-views precedent): registry strings narrow through the kit's
 * generated vocabularies, consent-free surfaces only (health's integrations
 * have no consent flow yet). No fetching, no state — mapping only.
 */

export type WireSyncStatus = {
  status?: string | null;
  last_success_at?: string | null;
  consecutive_failures?: number | null;
  conditions?: string[] | null;
};

export function capabilityViews(ids: string[] | undefined | null): IntegrationCapabilityView[] {
  return (ids ?? []).flatMap((id) => (isExtensionCapabilityId(id) ? [{ id }] : []));
}

export function kindOf(kind: string | undefined | null): ExtensionKind | null {
  return (EXTENSION_KINDS as readonly string[]).includes(kind ?? '')
    ? (kind as ExtensionKind)
    : null;
}

export function categoryViews(categories: string[] | undefined | null): ExtensionCategory[] {
  return (categories ?? []).filter((category): category is ExtensionCategory =>
    (EXTENSION_CATEGORIES as readonly string[]).includes(category),
  );
}

export function tierFor(capabilityIds: string[] | undefined | null): ExtensionTier {
  const lowest = (capabilityIds ?? []).map(
    (id) => EXTENSION_CAPABILITY_TIERS[id]?.[0] ?? 0,
  );
  return `T${Math.max(0, ...lowest)}` as ExtensionTier;
}

/** Family status first (the /active + /details sync_status surface); the
 * legacy status string is the fallback for rows that predate it. */
export function statusOf(
  syncStatus?: WireSyncStatus | null,
  legacyStatus?: string | null,
): ExtensionStatusState | null {
  const declared = syncStatus?.status;
  if (declared && isExtensionStatusState(declared)) return declared;
  if (legacyStatus === 'ACTIVE' || legacyStatus === 'active') return 'ok';
  if (legacyStatus === 'ERROR' || legacyStatus === 'error') return 'failing';
  return 'stale';
}

export function conditionViews(conditions: string[] | undefined | null): IntegrationConditionId[] {
  return (conditions ?? []).filter(
    (condition): condition is IntegrationConditionId =>
      condition === 'needs-config' || condition === 'needs-secret',
  );
}

export function catalogEntry(manifest: IntegrationManifest): IntegrationCatalogEntryView {
  return {
    id: manifest.domain,
    name: manifest.name || manifest.domain,
    version: manifest.version ?? null,
    summary: manifest.description ?? null,
    originClass: 'bundled',
    tier: tierFor(manifest.capabilities),
    kind: kindOf(manifest.kind),
    categories: categoryViews(manifest.categories),
    capabilities: capabilityViews(manifest.capabilities),
    provenance: manifest.author ? { author: manifest.author } : null,
    metadata: { accessType: manifest.access_type ?? null, icon: manifest.icon ?? null },
  };
}

export function instanceGroups(
  active: ActiveIntegration[],
  manifests: IntegrationManifest[],
): IntegrationExtensionGroupView[] {
  const byDomain = new Map<string, IntegrationManifest>();
  for (const manifest of manifests) byDomain.set(manifest.domain, manifest);
  const groups = new Map<string, IntegrationInstanceView[]>();
  for (const integration of active) {
    const manifest = byDomain.get(integration.domain);
    const rows = groups.get(integration.domain) ?? [];
    rows.push({
      id: integration.id,
      extensionId: integration.domain,
      name: integration.instance_name || manifest?.name || integration.domain,
      enabled: integration.status === 'ACTIVE' || integration.status === 'active',
      status: statusOf(integration.sync_status, integration.status),
      lastSuccessAt:
        integration.sync_status?.last_success_at ?? integration.last_synced_at ?? null,
      consecutiveFailures: integration.sync_status?.consecutive_failures ?? null,
      conditions: conditionViews(integration.sync_status?.conditions),
      originClass: 'bundled',
      tier: tierFor(manifest?.capabilities),
      metadata: { domain: integration.domain },
    });
    groups.set(integration.domain, rows);
  }
  return [...groups.entries()].map(([domain, instances]) => {
    const manifest = byDomain.get(domain);
    const extension: IntegrationExtensionView = {
      id: domain,
      name: manifest?.name || domain,
      version: manifest?.version ?? null,
      summary: manifest?.description ?? null,
      originClass: 'bundled',
      tier: tierFor(manifest?.capabilities),
      kind: kindOf(manifest?.kind),
      categories: categoryViews(manifest?.categories),
      capabilities: capabilityViews(manifest?.capabilities),
      provenance: manifest?.author ? { author: manifest.author } : null,
      metadata: { domain },
    };
    return { extension, instances };
  });
}

export function detailExtension(
  details: { domain: string; instance_name?: string | null },
  manifest: IntegrationManifest | undefined,
): IntegrationExtensionView {
  return {
    id: details.domain,
    name: details.instance_name || manifest?.name || details.domain,
    version: manifest?.version ?? null,
    summary: manifest?.description ?? null,
    originClass: 'bundled',
    tier: tierFor(manifest?.capabilities),
    kind: kindOf(manifest?.kind),
    categories: categoryViews(manifest?.categories),
    capabilities: capabilityViews(manifest?.capabilities),
    provenance: manifest?.author ? { author: manifest.author } : null,
    metadata: { domain: details.domain, accessType: manifest?.access_type ?? null },
  };
}

/** Health's per-extension docs tree (the extensionDocsTree form, plan 34
 * §10) as the kit's generic node tree; file nodes carry the file path as
 * their id. Category/item labels resolve through the tree's own i18n map
 * when present (requested locale, then defaultLocale), else the inline
 * title override, else the title-cased slug — the family string rule,
 * minimal form. */
export function docsNodes(
  tree: IntegrationDocsTree | undefined | null,
  locale?: string | null,
): IntegrationDocsNode[] {
  const i18n = tree?.i18n;
  const label = (key: string, inline: string | undefined, slug: string): string => {
    const localeMap = (code?: string | null) =>
      code ? i18n?.locales?.[code]?.[key] : undefined;
    return (
      localeMap(locale) ??
      localeMap(i18n?.defaultLocale) ??
      inline ??
      slug.replace(/(^|-)([a-z])/g, (_, sep: string, ch: string) =>
        sep ? ` ${ch.toUpperCase()}` : ch.toUpperCase(),
      )
    );
  };
  return (tree?.categories ?? []).map((category) => ({
    id: `category:${category.category}`,
    title: label(`category.${category.category}`, category.title, category.category),
    children: category.items.map((item) => ({
      id: item.file,
      title: label(`item.${item.id}`, item.title, item.id),
    })),
  }));
}

export function firstDocFile(nodes: IntegrationDocsNode[]): string | null {
  for (const node of nodes) {
    for (const child of node.children ?? []) {
      if (!child.id.startsWith('category:')) return child.id;
    }
  }
  return null;
}

/** The landing rule (plan 34 §10.4): the item carrying `index: true`, else
 * the first item of the first category. Applied to the raw tree (the kit
 * node list carries no index flag). */
export function landingDocFile(
  tree: IntegrationDocsTree | undefined | null,
): string | null {
  const items = (tree?.categories ?? []).flatMap((category) => category.items);
  const flagged = items.find((item) => item.index);
  return (flagged ?? items[0])?.file ?? null;
}

/** The audience tag of the item owning `file` (plan 34 §10.6) — shown by
 * the dialog as a badge when present; never gates access. */
export function audienceOfFile(
  tree: IntegrationDocsTree | undefined | null,
  file: string | null,
): 'user' | 'dev' | null {
  if (!file) return null;
  for (const category of tree?.categories ?? []) {
    for (const item of category.items) {
      if (item.file === file) return item.audience ?? null;
    }
  }
  return null;
}
