/**
 * Per-type single-record detail registry — the overlay counterpart of the
 * browse {@link viewRegistry}. Where `viewRegistry` maps a type to its full
 * list+detail browse view, this maps a type to a **single-record detail**
 * component (one `id` → the entity's rich, purpose-built preview). It exists
 * so `InstanceCard`'s "open" overlay can render the same single source of
 * truth the entity's own pages use (e.g. `ExaminationPreview`) instead of the
 * thin generic `InstancePreview`.
 *
 * Like the view registry, this is kept separate from the data adapters so
 * importing an adapter never drags in heavy entity UI (and never creates an
 * import cycle). Detail components register themselves from
 * `features/instances/details/index.ts`, imported once at app entry alongside
 * the views barrel.
 */
import type React from 'react';
import type { InstanceType } from './types';

export interface InstanceDetailProps {
  /** The record id to render in full. */
  id: string;
  /** Patient scope (passed through for patient-scoped lookups). */
  patientId?: string;
}

const details = new Map<InstanceType, React.ComponentType<InstanceDetailProps>>();

/** Register a per-type single-record detail view (replaces on re-register). */
export function registerInstanceDetail(
  type: InstanceType,
  Detail: React.ComponentType<InstanceDetailProps>,
): void {
  details.set(type, Detail);
}

/**
 * Resolve a per-type detail component, or `null` to fall back to the generic
 * {@link InstancePreview} (used for types that don't yet register a rich
 * detail view).
 */
export function getInstanceDetail(
  type: InstanceType,
): React.ComponentType<InstanceDetailProps> | null {
  return details.get(type) ?? null;
}

/** Test-only: clear the detail registry. */
export function _clearDetailsForTests(): void {
  details.clear();
}
