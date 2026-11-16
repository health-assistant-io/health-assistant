/**
 * The write-payload allowlist (plan A Phase 2 — ADR 0055 D5).
 *
 * Pins the contract the workspace save flow relies on: the draft is filtered
 * to exactly the descriptor-declared create/update fields (system-owned and
 * UI-only keys can never 422 the extra="forbid" backend), update payloads are
 * diffs against the original row, and stratified reference ranges stay a
 * domain operation (the post-save sync is their only writer).
 */
import { describe, it, expect } from 'vitest';
import { toWritePayload } from '../catalogWritePayload';
import type { CatalogItem, CatalogWriteMeta } from '../../../types/catalog';

const CONCEPT_WRITE: CatalogWriteMeta = {
  create_fields: [
    'aliases',
    'code',
    'coding_system',
    'color',
    'description',
    'display_order',
    'icon',
    'kind',
    'kinds',
    'meta_data',
    'name',
    'parent_id',
    'primary_kind',
    'slug',
    'tenant_scoped',
  ],
  update_fields: [
    'aliases',
    'code',
    'coding_system',
    'color',
    'description',
    'display_order',
    'icon',
    'kinds',
    'meta_data',
    'name',
    'parent_id',
    'primary_kind',
    'status',
  ],
  supports_promote: false,
};

const MEDICATION_WRITE: CatalogWriteMeta = {
  create_fields: [
    'class_concept_id',
    'class_concept_slug',
    'code',
    'coding_system',
    'contraindications',
    'description',
    'dosage_info',
    'indications',
    'name',
    'side_effects',
  ],
  update_fields: [
    'class_concept_id',
    'class_concept_slug',
    'code',
    'coding_system',
    'contraindications',
    'description',
    'dosage_info',
    'indications',
    'name',
    'side_effects',
  ],
  supports_promote: true,
};

const BIOMARKER_WRITE: CatalogWriteMeta = {
  create_fields: [
    'aliases',
    'allowed_states',
    'category',
    'class_concept_id',
    'class_concept_slug',
    'code',
    'coding_system',
    'description',
    'info',
    'is_telemetry',
    'name',
    'preferred_unit_id',
    'preferred_unit_symbol',
    'reference_range_max',
    'reference_range_min',
    'reference_ranges',
    'slug',
    'supports_multi_state',
    'value_type',
  ],
  update_fields: [
    'aliases',
    'allowed_states',
    'category',
    'class_concept_id',
    'class_concept_slug',
    'code',
    'coding_system',
    'description',
    'info',
    'is_telemetry',
    'name',
    'preferred_unit_id',
    'preferred_unit_symbol',
    'reference_range_max',
    'reference_range_min',
    'supports_multi_state',
    'value_type',
  ],
  supports_promote: true,
};

const VACCINE_WRITE: CatalogWriteMeta = {
  create_fields: [
    'class_concept_id',
    'class_concept_slug',
    'code',
    'coding_system',
    'contraindications',
    'description',
    'dose_schedule',
    'name',
    'side_effects',
  ],
  update_fields: [
    'class_concept_id',
    'class_concept_slug',
    'code',
    'coding_system',
    'contraindications',
    'description',
    'dose_schedule',
    'name',
    'side_effects',
  ],
  supports_promote: true,
};

describe('toWritePayload — create', () => {
  it('keeps only the declared fields, dropping UI-only and system-owned keys', () => {
    const draft: CatalogItem = {
      id: 'c-1',
      name: 'Cardiology',
      slug: 'cardiology',
      kinds: ['specialty'],
      tenant_scoped: false,
      description: 'Heart specialty',
      seed_key: 'concept:cardiology',
      scope: 'system',
      tenant_id: null,
      created_by: 'user-1',
      created_at: '2026-11-09T00:00:00Z',
      updated_at: '2026-11-09T00:00:00Z',
      version: 3,
      parent_slug: null,
      class_concept_name: 'Should never be sent',
      relation_count: 2,
      relation_breakdown: { PART_OF: 2 },
      links: [{ type: 'concept', id: 'p-1', label: 'Parent' }],
    };
    const payload = toWritePayload('concept', draft, 'create', CONCEPT_WRITE);
    expect(payload).toEqual({
      name: 'Cardiology',
      slug: 'cardiology',
      kinds: ['specialty'],
      tenant_scoped: false,
      description: 'Heart specialty',
    });
  });

  it('keeps typed form values verbatim (codes, states, ranges min/max, unit id)', () => {
    const draft = {
      name: 'Glucose',
      slug: 'glucose',
      coding_system: 'loinc',
      code: '1558-6',
      aliases: ['FBS'],
      preferred_unit_id: 'unit-1',
      reference_range_min: 70,
      reference_range_max: 100,
      is_telemetry: false,
      value_type: 'quantity',
      allowed_states: [],
      links: [{ type: 'anatomy', id: 'a-1', label: 'Blood' }],
      class_concept_name: 'Laboratory',
    };
    const payload = toWritePayload('biomarker', draft, 'create', BIOMARKER_WRITE);
    expect(payload).toEqual({
      name: 'Glucose',
      slug: 'glucose',
      coding_system: 'loinc',
      code: '1558-6',
      aliases: ['FBS'],
      preferred_unit_id: 'unit-1',
      reference_range_min: 70,
      reference_range_max: 100,
      is_telemetry: false,
      value_type: 'quantity',
      allowed_states: [],
    });
  });

  it('drops the stratified reference_ranges draft (the post-save sync writes them)', () => {
    const ranges = [{ sex: 'female', age_min: 18, low: 12, high: 16 }];
    const draft = {
      name: 'Hemoglobin',
      slug: 'hemoglobin',
      reference_ranges: ranges,
    };
    const payload = toWritePayload('biomarker', draft, 'create', BIOMARKER_WRITE);
    expect(payload).toEqual({ name: 'Hemoglobin', slug: 'hemoglobin' });
    expect(payload.reference_ranges).toBeUndefined();
  });

  it('drops the client-typed vaccine slug (server-derived, display-only)', () => {
    const draft = { name: 'MMR', slug: 'mmr', code: '03', side_effects: ['Fever'] };
    const payload = toWritePayload('vaccine', draft, 'create', VACCINE_WRITE);
    expect(payload).toEqual({ name: 'MMR', code: '03', side_effects: ['Fever'] });
  });

  it('sends only fields present in the draft — no fabricated nulls', () => {
    const payload = toWritePayload('medication', { name: 'Aspirin' }, 'create', MEDICATION_WRITE);
    expect(payload).toEqual({ name: 'Aspirin' });
  });

  it('is fail-safe without write metadata (empty payload, never a stale-body 422)', () => {
    const payload = toWritePayload('medication', { name: 'Aspirin' }, 'create', undefined);
    expect(payload).toEqual({});
  });
});

describe('toWritePayload — update (diff against the original row)', () => {
  const original: CatalogItem = {
    id: 'c-1',
    name: 'Old Name',
    slug: 'old-name',
    kinds: ['disease'],
    status: 'active',
    meta_data: { source: 'seed' },
    display_order: 0,
    seed_key: 'concept:old-name',
    scope: 'system',
    tenant_id: null,
  };

  it('sends only changed declared fields; response-only keys never pass', () => {
    const draft = { ...original, name: 'New Name' };
    const payload = toWritePayload('concept', draft, 'update', CONCEPT_WRITE, original);
    expect(payload).toEqual({ name: 'New Name' });
  });

  it('drops unchanged server echoes that the update schema would reject (biomarker value_type)', () => {
    const bioOriginal: CatalogItem = {
      id: 'b-1',
      name: 'Glucose',
      value_type: 'quantity',
      is_telemetry: false,
      supports_multi_state: false,
      slug: 'glucose',
    };
    const draft = { ...bioOriginal, name: 'Glucose (fasting)' };
    const payload = toWritePayload('biomarker', draft, 'update', BIOMARKER_WRITE, bioOriginal);
    expect(payload).toEqual({ name: 'Glucose (fasting)' });
    expect(payload.value_type).toBeUndefined();
  });

  it('sends a declared field whose value actually changed (status toggle)', () => {
    const draft = { ...original, status: 'retired' };
    const payload = toWritePayload('concept', draft, 'update', CONCEPT_WRITE, original);
    expect(payload).toEqual({ status: 'retired' });
  });

  it('drops keys the update schema does not declare even when they changed (concept slug)', () => {
    const draft = { ...original, name: 'New Name', slug: 'renamed' };
    const payload = toWritePayload('concept', draft, 'update', CONCEPT_WRITE, original);
    expect(payload).toEqual({ name: 'New Name' });
  });

  it('compares arrays/objects by value, not identity', () => {
    const sameContent = { ...original, kinds: ['disease'] as string[] };
    expect(
      toWritePayload('concept', sameContent, 'update', CONCEPT_WRITE, original),
    ).toEqual({});
    const changedKinds = { ...original, kinds: ['disease', 'symptom'] };
    expect(
      toWritePayload('concept', changedKinds, 'update', CONCEPT_WRITE, original),
    ).toEqual({ kinds: ['disease', 'symptom'] });
  });

  it('compares objects key-order-insensitively (anatomy display maps)', () => {
    const anatomyWrite: CatalogWriteMeta = {
      create_fields: ['display', 'name', 'slug'],
      update_fields: ['display', 'name', 'slug'],
      supports_promote: true,
    };
    const row: CatalogItem = {
      ...original,
      display: { map: { marker: { view: 'front' } }, color: 'red' },
    };
    const reordered = {
      ...row,
      display: { color: 'red', map: { marker: { view: 'front' } } },
    } as CatalogItem;
    expect(toWritePayload('anatomy', reordered, 'update', anatomyWrite, row)).toEqual({});
    const changed = { ...row, display: { map: { marker: { view: 'back' } }, color: 'red' } };
    expect(toWritePayload('anatomy', changed, 'update', anatomyWrite, row)).toEqual({
      display: { map: { marker: { view: 'back' } }, color: 'red' },
    });
  });

  it('sends an explicit null where the original holds a value (reference clear)', () => {
    const bioOriginal: CatalogItem = {
      id: 'b-1',
      name: 'Glucose',
      slug: 'glucose',
      preferred_unit_id: 'unit-1',
      class_concept_id: 'c-9',
    };
    const draft = {
      ...bioOriginal,
      preferred_unit_id: null,
      class_concept_id: null,
    };
    const payload = toWritePayload('biomarker', draft, 'update', BIOMARKER_WRITE, bioOriginal);
    // The backend clears reference links on explicit nulls (model_fields_set).
    expect(payload).toEqual({ preferred_unit_id: null, class_concept_id: null });
    // And a null that was already null is not re-sent.
    const cleared = toWritePayload('biomarker', draft, 'update', BIOMARKER_WRITE, {
      ...bioOriginal,
      preferred_unit_id: null,
      class_concept_id: null,
    });
    expect(cleared).toEqual({});
  });

  it('update mode without an original throws (never a full-field body)', () => {
    const draft = { name: 'Aspirin', side_effects: ['Nausea'], seed_key: 'leak-me' };
    expect(() =>
      toWritePayload('medication', draft, 'update', MEDICATION_WRITE, null),
    ).toThrowError(/original row/i);
    expect(() =>
      toWritePayload('medication', draft, 'update', MEDICATION_WRITE, undefined),
    ).toThrowError(/original row/i);
  });
});
