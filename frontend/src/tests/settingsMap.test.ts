import { describe, expect, it } from 'vitest';

import {
  systemSettingsNav,
  tenantSettingsNav,
} from '../config/settingsNav';
import { AI_TABS } from '../pages/Settings/AIConfig';
import { TOOLS_TAB_SECTIONS } from '../pages/Settings/ToolsConfig';
// eslint-disable-next-line import/extensions
import mapJson from '../../../contracts/settings-map.json';

// Settings-structure conformance wall (ADR-0033 / plan 27): the settings
// nav in this app must match the family contract vendored at
// core/contracts/settings-map.json. Placement changes go through the dev
// canonical copy first — never by editing the vendored file (which
// scripts/verify-wiring.sh keeps byte-identical) or the nav arrays alone.
const map = mapJson as {
  products: Record<
    string,
    {
      tabs: {
        id: string;
        group: string;
        admin?: boolean;
        sections?: { id: string; domains: string[]; status?: string }[];
      }[];
    }
  >;
};

const declared = map.products['health-assistant/core'].tabs;

const declaredSections = (tabId: string) =>
  (declared.find((tab) => tab.id === tabId)?.sections ?? []).map(
    (section) => section.id,
  );

describe('settings-map contract', () => {
  it('the declared ai-config sections match the app’s AI tabs', () => {
    expect(declaredSections('ai-config')).toEqual([...AI_TABS]);
  });

  it('the declared tools sections match the app’s tools sections', () => {
    expect(declaredSections('tools')).toEqual([...TOOLS_TAB_SECTIONS]);
  });

  // INV-1 (enforced part): aligned sections host only their own group's
  // domains — agent behavior never drifts back into the AI config.
  it('the ai-config tab hosts only ai.* domains', () => {
    const ai = declared.find((tab) => tab.id === 'ai-config');
    expect(ai).toBeDefined();
    for (const section of ai!.sections ?? []) {
      expect(section.status ?? 'aligned').toBe('aligned');
      for (const domain of section.domains) {
        expect(domain.startsWith('ai.')).toBe(true);
      }
    }
  });

  it('the tools tab hosts only tools.* domains', () => {
    const tools = declared.find((tab) => tab.id === 'tools');
    expect(tools).toBeDefined();
    for (const section of tools!.sections ?? []) {
      expect(section.status ?? 'aligned').toBe('aligned');
      for (const domain of section.domains) {
        expect(domain.startsWith('tools.')).toBe(true);
      }
    }
  });

  it('the tier navs expose the tools page next to AI configuration', () => {
    expect(
      tenantSettingsNav.some((item) => item.to === '/admin/tenant/tools'),
    ).toBe(true);
    expect(
      systemSettingsNav.some((item) => item.to === '/admin/system/tools'),
    ).toBe(true);
  });

  it('the user-tier nav has no tools entry (agent settings are tier-scoped)', () => {
    // The agent-behavior setting is tenant/system-scoped; the user nav
    // must not advertise a tools surface that does not exist.
    expect(
      tenantSettingsNav.filter((item) => item.to.endsWith('/tools')).length,
    ).toBe(1);
  });
});
