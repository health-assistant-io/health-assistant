/**
 * i18n key audit for the browse-create change (definitions are created from
 * the catalog browse dialog — no inline define-new forms).
 *
 * Pins, in BOTH locales (en + el):
 *   - the inline define-new keys are GONE (deleted with their surfaces);
 *   - `anatomy.add_custom` STAYS (its surface lives on);
 *   - the new `catalogs.create_new_item` / `create_new_hint` /
 *     `catalogs.<type>.label` keys exist (the labels match the backend
 *     registrations' `ui.label_key` names);
 *   - `ai_chat.hitl.define_suggested_item` exists (the HITL unmatched-proposal
 *     chip that opens the same create dialog);
 *   - `medications.modal.not_in_catalog` is a full standalone toast message
 *     with the `{{name}}` interpolation (Magic Fill unmatched).
 */
import { describe, it, expect } from 'vitest';
import en from '../locales/en/common.json';
import el from '../locales/el/common.json';

type Node = Record<string, unknown>;

const get = (root: Node, path: string): unknown =>
  path.split('.').reduce<unknown>((acc, k) => {
    if (acc && typeof acc === 'object') return (acc as Node)[k];
    return undefined;
  }, root);

const DELETED_KEYS = [
  'medications.add_custom_title',
  'examination_detail.add_biomarker.create_new_definition',
  'medications.modal.define_new',
  'medications.modal.add_custom',
  'medications.modal.change',
  'medications.modal.name_label',
  'medications.modal.name_placeholder',
  'medications.modal.main_indications',
  'medications.modal.indications_q',
  'medications.modal.side_effects',
  'medications.modal.add_side_effect',
  'allergies.modal.define_new',
  'allergies.modal.add_custom',
  'allergies.modal.name_label',
  'allergies.modal.description_label',
  'allergies.modal.typical_reactions',
  'allergies.modal.add_reaction_placeholder',
  'allergies.modal.category_label',
  'vaccinations.modal.define_new',
  'vaccinations.modal.add_custom',
  'vaccinations.modal.name_label',
  'vaccinations.modal.name_placeholder',
];

const NEW_KEYS = [
  'catalogs.create_new_item',
  'catalogs.create_new_hint',
  'catalogs.medication.label',
  'catalogs.allergy.label',
  'catalogs.vaccine.label',
  'catalogs.biomarker.label',
  'ai_chat.hitl.define_suggested_item',
];

describe('i18n key audit (browse-create)', () => {
  it('deleted the inline define-new keys in both locales', () => {
    for (const key of DELETED_KEYS) {
      expect(get(en, key), `en still has ${key}`).toBeUndefined();
      expect(get(el, key), `el still has ${key}`).toBeUndefined();
    }
  });

  it('keeps anatomy.add_custom (its surface lives on)', () => {
    expect(get(en, 'anatomy.add_custom')).toBeTruthy();
    expect(get(el, 'anatomy.add_custom')).toBeTruthy();
  });

  it('added the browse-create keys in both locales (non-empty)', () => {
    for (const key of NEW_KEYS) {
      expect(typeof get(en, key)).toBe('string');
      expect(String(get(en, key)).length).toBeGreaterThan(0);
      expect(typeof get(el, key)).toBe('string');
      expect(String(get(el, key)).length).toBeGreaterThan(0);
    }
  });

  it('labels the four creatable types (matching the backend label_key names)', () => {
    expect(get(en, 'catalogs.medication.label')).toBe('Medication');
    expect(get(en, 'catalogs.allergy.label')).toBe('Allergen');
    expect(get(en, 'catalogs.vaccine.label')).toBe('Vaccine');
    expect(get(en, 'catalogs.biomarker.label')).toBe('Biomarker');
  });

  it('create_new_item interpolates the type label', () => {
    expect(String(get(en, 'catalogs.create_new_item'))).toContain('{{type}}');
    expect(String(get(el, 'catalogs.create_new_item'))).toContain('{{type}}');
  });

  it('define_suggested_item interpolates both the type label and the proposed name', () => {
    for (const loc of [en, el]) {
      const msg = String(get(loc, 'ai_chat.hitl.define_suggested_item'));
      expect(msg).toContain('{{type}}');
      expect(msg).toContain('{{name}}');
    }
  });

  it('not_in_catalog is a standalone toast with the {{name}} interpolation', () => {
    for (const loc of [en, el]) {
      const msg = String(get(loc, 'medications.modal.not_in_catalog'));
      expect(msg).toContain('{{name}}');
      expect(msg.length).toBeGreaterThan(20);
    }
  });
});
