/**
 * Per-type browse-view registry — the UI counterpart of the data adapter
 * registry. Keeps view components (which carry heavy entity-UI transitive
 * imports, e.g. ExaminationCard → AssociatedEvents → …) OUT of the adapter
 * modules, so importing an adapter never drags in UI (and never creates an
 * import cycle). Views register themselves from
 * `features/instances/views/index.ts`, which is imported once at app entry
 * alongside the adapters barrel.
 */
import type React from 'react';
import type { InstanceType, InstanceViewProps } from './types';

const views = new Map<InstanceType, React.ComponentType<InstanceViewProps<any>>>();

/** Register a per-type browse view (replaces on re-register; test-friendly). */
export function registerInstanceView<T>(
  type: InstanceType,
  View: React.ComponentType<InstanceViewProps<T>>,
): void {
  views.set(type, View as React.ComponentType<InstanceViewProps<any>>);
}

/** Resolve a per-type view, or `null` to fall back to the generic browser. */
export function getInstanceView(
  type: InstanceType,
): React.ComponentType<InstanceViewProps<any>> | null {
  return views.get(type) ?? null;
}

/** Test-only: clear the view registry. */
export function _clearViewsForTests(): void {
  views.clear();
}
