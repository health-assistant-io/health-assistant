import { describe, it, expect } from 'vitest';

import {
  capabilityViews,
  catalogEntry,
  categoryViews,
  conditionViews,
  detailExtension,
  docsNodes,
  firstDocFile,
  landingDocFile,
  audienceOfFile,
  instanceGroups,
  kindOf,
  statusOf,
  tierFor,
} from './integrationViews';
import type {
  ActiveIntegration,
  IntegrationManifest,
} from '../../services/integrationService';

const manifest = (overrides: Partial<IntegrationManifest> = {}): IntegrationManifest => ({
  domain: 'webhook',
  name: 'Webhook Receiver',
  version: '1.2.0',
  integration_type: ['pull', 'push'],
  description: 'Receives webhook events.',
  author: 'Core',
  access_type: 'cloud',
  categories: ['data', 'productivity', 'mystery-category'],
  kind: 'connector',
  capabilities: ['net', 'inbound-webhook', 'teleport'],
  ...overrides,
});

describe('integrationViews (wire → kit mapping)', () => {
  it('narrows capability ids through the generated vocab and drops unknown ones', () => {
    const views = capabilityViews(['net', 'teleport', 'inbound-webhook']);
    expect(views).toEqual([{ id: 'net' }, { id: 'inbound-webhook' }]);
    expect(capabilityViews(undefined)).toEqual([]);
  });

  it('narrows kinds and categories to the charter vocabularies', () => {
    expect(kindOf('connector')).toBe('connector');
    expect(kindOf('gadget')).toBeNull();
    expect(categoryViews(['data', 'mystery', 'learning'])).toEqual(['data', 'learning']);
  });

  it('derives the tier from the capability-tier table', () => {
    expect(tierFor(['net'])).toBe('T1');
    expect(tierFor(['secrets'])).toBe('T0');
    expect(tierFor(['net', 'secrets'])).toBe('T1');
    expect(tierFor([])).toBe('T0');
  });

  it('maps family status first with the legacy string as fallback', () => {
    expect(statusOf({ status: 'degraded' }, 'ACTIVE')).toBe('degraded');
    expect(statusOf({ status: 'mystery' }, 'ACTIVE')).toBe('ok');
    expect(statusOf(null, 'ERROR')).toBe('failing');
    expect(statusOf(null, 'PENDING')).toBe('stale');
    expect(statusOf({ status: 'ok' })).toBe('ok');
  });

  it('keeps only the known conditions', () => {
    expect(conditionViews(['needs-config', 'mystery'])).toEqual(['needs-config']);
    expect(conditionViews(['needs-secret'])).toEqual(['needs-secret']);
  });

  it('maps a catalog entry with narrowed vocab, provenance and metadata round-trip', () => {
    const entry = catalogEntry(manifest());
    expect(entry.id).toBe('webhook');
    expect(entry.summary).toBe('Receives webhook events.');
    expect(entry.originClass).toBe('bundled');
    expect(entry.tier).toBe('T1');
    expect(entry.kind).toBe('connector');
    expect(entry.categories).toEqual(['data', 'productivity']);
    expect(entry.capabilities).toEqual([{ id: 'net' }, { id: 'inbound-webhook' }]);
    expect(entry.provenance).toEqual({ author: 'Core' });
    expect(entry.metadata).toEqual({ accessType: 'cloud', icon: null });
  });

  it('groups instances under their extension (G1) with family status', () => {
    const active: ActiveIntegration[] = [
      {
        id: 'inst-1',
        domain: 'webhook',
        instance_name: 'Garage door',
        status: 'ACTIVE',
        last_synced_at: '2026-10-01T10:00:00Z',
        sync_status: {
          status: 'degraded',
          last_success_at: '2026-10-10T10:00:00Z',
          consecutive_failures: 2,
          conditions: ['needs-config'],
        },
      },
      {
        id: 'inst-2',
        domain: 'webhook',
        instance_name: null,
        status: 'ERROR',
        last_synced_at: null,
      },
      {
        id: 'f-1',
        domain: 'fhir_server',
        instance_name: 'Hospital FHIR',
        status: 'ACTIVE',
        last_synced_at: null,
      },
    ];
    const groups = instanceGroups(active, [manifest(), manifest({ domain: 'fhir_server', name: 'FHIR Server', kind: undefined })]);
    expect(groups).toHaveLength(2);

    const webhook = groups.find((g) => g.extension.id === 'webhook');
    expect(webhook?.extension.name).toBe('Webhook Receiver');
    expect(webhook?.instances).toHaveLength(2);
    expect(webhook?.instances[0]).toMatchObject({
      id: 'inst-1',
      extensionId: 'webhook',
      name: 'Garage door',
      enabled: true,
      status: 'degraded',
      lastSuccessAt: '2026-10-10T10:00:00Z',
      consecutiveFailures: 2,
      conditions: ['needs-config'],
      metadata: { domain: 'webhook' },
    });
    expect(webhook?.instances[1]).toMatchObject({
      id: 'inst-2',
      name: 'Webhook Receiver',
      enabled: false,
      status: 'failing',
      lastSuccessAt: null,
    });

    const fhir = groups.find((g) => g.extension.id === 'fhir_server');
    expect(fhir?.extension.kind).toBeNull();
    expect(fhir?.instances[0].name).toBe('Hospital FHIR');
  });

  it('maps the detail extension with the instance name taking precedence', () => {
    const view = detailExtension({ domain: 'webhook', instance_name: 'Garage door' }, manifest());
    expect(view.name).toBe('Garage door');
    expect(view.id).toBe('webhook');
    expect(detailExtension({ domain: 'webhook' }, undefined).name).toBe('webhook');
  });

  it('maps the per-extension docs tree (extensionDocsTree form) into kit nodes keyed by file', () => {
    const tree = {
      version: 1,
      categories: [
        {
          category: 'overview',
          items: [{ id: 'a', file: 'docs/setup.md', title: 'Setup' }],
        },
        {
          category: 'usage',
          items: [{ id: 'b', file: 'docs/usage.md', title: 'Usage' }],
        },
      ],
    };
    const nodes = docsNodes(tree);
    expect(nodes).toEqual([
      {
        id: 'category:overview',
        title: 'Overview',
        children: [{ id: 'docs/setup.md', title: 'Setup' }],
      },
      {
        id: 'category:usage',
        title: 'Usage',
        children: [{ id: 'docs/usage.md', title: 'Usage' }],
      },
    ]);
    expect(firstDocFile(nodes)).toBe('docs/setup.md');
    expect(firstDocFile(docsNodes(undefined))).toBeNull();
  });

  it('resolves category/item labels through the tree i18n map, then inline titles, then slugs', () => {
    const tree = {
      version: 1,
      categories: [
        {
          category: 'overview',
          title: 'Inline Override',
          items: [{ id: 'connection', file: 'connection.md', title: 'Connect' }],
        },
        { category: 'usage', items: [{ id: 'pull', file: 'pull.md', title: 'Pull' }] },
      ],
      i18n: {
        defaultLocale: 'en',
        locales: {
          en: { 'category.overview': 'Getting Started' },
          de: { 'category.overview': 'Erste Schritte', 'item.connection': 'Server verbinden' },
        },
      },
    };
    expect(docsNodes(tree, 'de')[0].title).toBe('Erste Schritte');
    expect(docsNodes(tree, 'de')[0].children[0].title).toBe('Server verbinden');
    expect(docsNodes(tree, 'fr')[0].title).toBe('Getting Started'); // defaultLocale fallback
    // a category without any i18n/title falls back to the inline title,
    // and one without either to the title-cased slug
    expect(docsNodes(tree, 'de')[1].title).toBe('usage'.replace(/^./, (c) => c.toUpperCase()));
    const noInline = { ...tree, categories: [{ category: 'security', items: tree.categories[1].items }] };
    expect(docsNodes(noInline, 'de')[0].title).toBe('Security');
  });

  it('applies the landing rule: index flag wins, else first item of first category', () => {
    const tree = {
      version: 1,
      categories: [
        { category: 'overview', items: [{ id: 'a', file: 'overview.md', title: 'Overview' }] },
        {
          category: 'usage',
          items: [
            { id: 'b', file: 'tricks.md', title: 'Tricks', index: true },
            { id: 'c', file: 'pull.md', title: 'Pull' },
          ],
        },
      ],
    };
    expect(landingDocFile(tree)).toBe('tricks.md');
    const unflagged = {
      version: 1,
      categories: [
        { category: 'overview', items: [{ id: 'a', file: 'overview.md', title: 'Overview' }] },
      ],
    };
    expect(landingDocFile(unflagged)).toBe('overview.md');
    expect(landingDocFile(undefined)).toBeNull();
  });

  it('finds the audience tag of the current file, absent when untagged', () => {
    const tree = {
      version: 1,
      categories: [
        {
          category: 'overview',
          items: [
            { id: 'a', file: 'overview.md', title: 'Overview', audience: 'user' as const },
            { id: 'b', file: 'hacking.md', title: 'Hacking', audience: 'dev' as const },
            { id: 'c', file: 'plain.md', title: 'Plain' },
          ],
        },
      ],
    };
    expect(audienceOfFile(tree, 'overview.md')).toBe('user');
    expect(audienceOfFile(tree, 'hacking.md')).toBe('dev');
    expect(audienceOfFile(tree, 'plain.md')).toBeNull();
    expect(audienceOfFile(tree, null)).toBeNull();
  });
});
