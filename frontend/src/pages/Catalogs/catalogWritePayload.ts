/**
 * The one catalog write-path payload builder (plan A Phase 2 — ADR 0055 D5).
 *
 * Every workspace create/update goes through the generic `/catalogs/{type}`
 * endpoints, whose backend schemas are `extra="forbid"`: an unknown or
 * system-owned key is a 422, not a silent drop. `toWritePayload` filters the
 * form draft down to exactly the fields the active type's descriptor declares
 * for the mode — the `write` metadata from `GET /catalogs` is the single
 * source of truth, so UI-only keys (`links`, `relation_count`, display-only
 * `class_concept_name` / `parent_slug`) and system-owned keys (`id`, `scope`,
 * `tenant_id`, `created_by`, `seed_key`, timestamps — none of which sit on any
 * write schema) never reach the wire.
 *
 * Update payloads are diffs: only allowlisted keys whose value actually
 * changed vs the original row are sent. The backend update services patch via
 * `model_dump(exclude_unset=True)`, and several update schemas reject
 * server-echoed values outright (`BiomarkerUpdate.value_type` raises on any
 * non-null value — value-type changes are destructive and require a
 * dedicated migration) — so re-sending the row's own unchanged fields would
 * fail otherwise-valid saves. Without the original row there is no safe
 * baseline, so update mode **throws** rather than sending a full-field body
 * (callers fetch the row first; a row that cannot be fetched is gone and the
 * save must not proceed on a guess).
 *
 * An explicit `null` in the draft where the original row holds a value is a
 * real change and is sent — the backend clears reference links
 * (`preferred_unit_id` / `class_concept_id`) on explicit nulls.
 *
 * `reference_ranges` is excluded even though `BiomarkerCreate` declares it
 * (that declaration exists for the seed/import path): the workspace's
 * post-save `syncReferenceRanges` reconciliation is the single writer for
 * stratified ranges, and draft range rows carry no server ids — sending them
 * here would insert rows the sync then deletes and re-creates.
 */

import type { CatalogItem, CatalogTypeMeta } from '../../types/catalog';

export type CatalogWriteMode = 'create' | 'update';

/** Draft keys that are separate domain operations, never catalog write fields. */
const DOMAIN_OPERATION_FIELDS = new Set(['reference_ranges']);

const isPlainObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v);

/** Order-insensitive value comparison (objects compare by content, not key
 * order; arrays elementwise; null/undefined equivalent). */
const sameValue = (a: unknown, b: unknown): boolean => {
  const av = a ?? null;
  const bv = b ?? null;
  if (av === bv) return true;
  if (isPlainObject(av) && isPlainObject(bv)) {
    const ak = Object.keys(av).sort();
    const bk = Object.keys(bv).sort();
    if (ak.length !== bk.length) return false;
    if (ak.some((k, i) => k !== bk[i])) return false;
    return ak.every((k) => sameValue(av[k], bv[k]));
  }
  if (Array.isArray(av) && Array.isArray(bv)) {
    return av.length === bv.length && av.every((item, i) => sameValue(item, bv[i]));
  }
  return JSON.stringify(av) === JSON.stringify(bv);
};

/**
 * Build the request body for one catalog write.
 *
 * @param _type     The active catalog type (kept for call-site readability —
 *                  the allowlist itself comes from `writeMeta`).
 * @param draft     The form draft (the full catalog row in edit mode).
 * @param mode      'create' → the type's create fields; 'update' → its update
 *                  fields, diffed against `original`.
 * @param writeMeta The descriptor's write metadata (undefined → empty payload,
 *                  so a metadata-less backend can never 422 a stale client).
 * @param original  The unmodified server row in update mode (diff baseline).
 *                  Required in update mode — missing baseline throws.
 */
export function toWritePayload(
  _type: string,
  draft: CatalogItem,
  mode: CatalogWriteMode,
  writeMeta: CatalogTypeMeta['write'] | undefined,
  original?: CatalogItem | null,
): Record<string, unknown> {
  if (mode === 'update' && !original) {
    throw new Error(
      'Update requires the original row to diff against — fetch it before saving. ' +
        'Sending a full-field body would 422 on server-echoed values (e.g. biomarker value_type).',
    );
  }
  const fields =
    mode === 'create' ? writeMeta?.create_fields ?? [] : writeMeta?.update_fields ?? [];
  const payload: Record<string, unknown> = {};
  for (const field of fields) {
    if (DOMAIN_OPERATION_FIELDS.has(field)) continue;
    const value = draft[field];
    if (value === undefined) continue;
    if (mode === 'update' && sameValue(original![field], value)) continue;
    payload[field] = value;
  }
  return payload;
}
