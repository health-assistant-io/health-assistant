import { describe, it, expect } from 'vitest';
import {
  CARD_REGISTRY,
  getCardDefinition,
  resolveCardComponent,
  resolveDefaultConfig,
  resolveDefaultLayout,
  ADDABLE_CARDS,
} from '../cardRegistry';

describe('CARD_REGISTRY', () => {
  it('is non-empty', () => {
    expect(CARD_REGISTRY.length).toBeGreaterThan(0);
  });

  it('every entry has a unique type', () => {
    const types = CARD_REGISTRY.map((d) => d.type);
    expect(new Set(types).size).toBe(types.length);
  });

  it('every entry has a React component', () => {
    for (const def of CARD_REGISTRY) {
      expect(def.component).toBeDefined();
      expect(typeof def.component).toBe('object');
    }
  });

  it('every entry has a valid defaultLayout with w and h > 0', () => {
    for (const def of CARD_REGISTRY) {
      expect(def.defaultLayout.w).toBeGreaterThan(0);
      expect(def.defaultLayout.h).toBeGreaterThan(0);
    }
  });

  it('every entry has a non-empty labelKey', () => {
    for (const def of CARD_REGISTRY) {
      expect(def.labelKey).toBeTruthy();
      expect(def.labelKey.startsWith('dashboard.cards.')).toBe(true);
    }
  });
});

describe('getCardDefinition', () => {
  it('resolves known types', () => {
    expect(getCardDefinition('biomarker')?.type).toBe('biomarker');
    expect(getCardDefinition('trends')?.type).toBe('trends');
    expect(getCardDefinition('labs')?.type).toBe('labs');
  });

  it('resolves aliases to the canonical definition', () => {
    const def = getCardDefinition('medication_calendar');
    expect(def).toBeDefined();
    expect(def?.type).toBe('health_calendar');
  });

  it('returns undefined for unknown types', () => {
    expect(getCardDefinition('nonexistent')).toBeUndefined();
  });
});

describe('resolveCardComponent', () => {
  it('returns a component for every registered type', () => {
    for (const def of CARD_REGISTRY) {
      expect(resolveCardComponent(def.type)).toBeDefined();
    }
  });

  it('returns undefined for unknown types', () => {
    expect(resolveCardComponent('does_not_exist')).toBeUndefined();
  });
});

describe('resolveDefaultConfig', () => {
  it('returns static defaultConfig for non-biomarker cards', () => {
    const config = resolveDefaultConfig('imaging', { defaultBiomarker: 'glucose', biomarkerLabel: 'Glucose' });
    expect(config).toEqual({});
  });

  it('uses resolveDefaultConfig for biomarker cards', () => {
    const config = resolveDefaultConfig('biomarker', { defaultBiomarker: 'cholesterol', biomarkerLabel: 'Cholesterol' });
    expect(config.biomarker).toBe('cholesterol');
    expect(config.icon).toBeTruthy();
  });

  it('returns empty object for unknown types', () => {
    expect(resolveDefaultConfig('unknown', { defaultBiomarker: '', biomarkerLabel: '' })).toEqual({});
  });
});

describe('resolveDefaultLayout', () => {
  it('returns the registered layout for known types', () => {
    const layout = resolveDefaultLayout('trends');
    expect(layout.w).toBeGreaterThan(0);
    expect(layout.h).toBeGreaterThan(0);
  });

  it('returns a fallback for unknown types', () => {
    const layout = resolveDefaultLayout('unknown_type');
    expect(layout.w).toBeGreaterThan(0);
    expect(layout.h).toBeGreaterThan(0);
  });
});

describe('ADDABLE_CARDS', () => {
  it('includes all registered cards', () => {
    expect(ADDABLE_CARDS.length).toBe(CARD_REGISTRY.length);
  });
});
