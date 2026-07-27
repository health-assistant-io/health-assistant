import { describe, it, expect } from 'vitest';
import type { NotificationKindState } from '../../services/notificationService';

/**
 * The grouping helper that drives the "Per integration" section of the
 * NotificationSettings hub. Extracted here as a pure function so it can be
 * tested in isolation — mirrors the helper inside NotificationSettings.tsx.
 */
function groupIntegrationKindsByInstance(
  kinds: NotificationKindState[]
): {
  manageUrl: string;
  instanceName: string;
  kinds: NotificationKindState[];
}[] {
  const byUrl = new Map<string, { manageUrl: string; instanceName: string; kinds: NotificationKindState[] }>();
  for (const k of kinds) {
    const instanceName = k.label.split(' — ').pop() ?? k.label;
    if (!byUrl.has(k.manage_url)) {
      byUrl.set(k.manage_url, { manageUrl: k.manage_url, instanceName, kinds: [] });
    }
    byUrl.get(k.manage_url)!.kinds.push(k);
  }
  return Array.from(byUrl.values());
}

function makeKind(
  kindId: string,
  label: string,
  manageUrl: string,
  enabled = true
): NotificationKindState {
  return {
    kind_id: kindId,
    label,
    group: 'integration',
    manage_url: manageUrl,
    mutable: true,
    default_enabled: true,
    enabled,
  };
}

describe('groupIntegrationKindsByInstance', () => {
  it('groups kinds sharing the same manage_url into one instance', () => {
    const url = '/settings/integrations/abc?tab=notifications';
    const kinds = [
      makeKind('integration:abc:sensor', 'Sensor malfunction — My Band', url),
      makeKind('integration:abc:summary', 'Daily summary — My Band', url),
    ];

    const groups = groupIntegrationKindsByInstance(kinds);

    expect(groups).toHaveLength(1);
    expect(groups[0].instanceName).toBe('My Band');
    expect(groups[0].manageUrl).toBe(url);
    expect(groups[0].kinds).toHaveLength(2);
  });

  it('keeps separate instances apart (per-instance muting contract)', () => {
    const urlA = '/settings/integrations/aaa?tab=notifications';
    const urlB = '/settings/integrations/bbb?tab=notifications';
    const kinds = [
      makeKind('integration:aaa:sensor', 'Sensor — Band A', urlA),
      makeKind('integration:bbb:sensor', 'Sensor — Band B', urlB),
    ];

    const groups = groupIntegrationKindsByInstance(kinds);

    expect(groups).toHaveLength(2);
    expect(groups[0].instanceName).toBe('Band A');
    expect(groups[1].instanceName).toBe('Band B');
  });

  it('falls back to the full label when no “ — ” separator is present', () => {
    const url = '/settings/integrations/abc?tab=notifications';
    const kinds = [makeKind('integration:abc:x', 'No separator here', url)];

    const groups = groupIntegrationKindsByInstance(kinds);

    expect(groups[0].instanceName).toBe('No separator here');
  });

  it('returns an empty list for no integration kinds', () => {
    expect(groupIntegrationKindsByInstance([])).toEqual([]);
  });
});
