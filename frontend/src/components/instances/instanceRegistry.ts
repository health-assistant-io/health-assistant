/**
 * Runtime registry of instance adapters. Mirrors the role of the catalog
 * `getFacetsForType` switch, but for richer adapter objects (which carry
 * fetch/search/toRow methods, not just facet data). A runtime `Map` keeps the
 * set open for extension — adding an entity type = `registerAdapter(...)` in
 * the adapters barrel, no edit to this file.
 *
 * The generic components resolve adapters by type here and cast to
 * `InstanceAdapter<unknown>`; entity-specific typing lives in the adapter
 * modules and their consumers. This is the same "polymorphic workspace, typed
 * adapter" pattern the catalog side uses.
 *
 * See `dev/plans/instance-browser-unified-picker-2026-07-16.md`.
 */
import type { InstanceAdapter, InstanceType } from './types';

const adapters = new Map<InstanceType, InstanceAdapter<unknown>>();

/**
 * Register an adapter. Called from the adapters barrel
 * (`features/instances/adapters/index.ts`) so importing that barrel wires the
 * whole set. Re-registering the same type replaces (useful in tests).
 */
export function registerAdapter<T>(adapter: InstanceAdapter<T>): void {
  adapters.set(adapter.type, adapter as InstanceAdapter<unknown>);
}

/**
 * Resolve the adapter for a type. Throws if none is registered — failing loud
 * is intentional: an unregistered type is a wiring bug, not a runtime state.
 */
export function getAdapter(type: InstanceType): InstanceAdapter<unknown> {
  const a = adapters.get(type);
  if (!a) {
    throw new Error(
      `[instanceRegistry] No adapter registered for instance type "${type}". ` +
        'Import features/instances/adapters to wire the default set.',
    );
  }
  return a;
}

/** Resolve adapters for a set of types (preserving order). `undefined` = all. */
export function getAdapters(types?: InstanceType[]): InstanceAdapter<unknown>[] {
  if (!types || types.length === 0) return Array.from(adapters.values());
  return types.map((t) => getAdapter(t));
}

/** True when an adapter is registered for the type. */
export function hasAdapter(type: InstanceType): boolean {
  return adapters.has(type);
}

/** Test-only: clear the registry (used by registry unit tests). */
export function _clearAdaptersForTests(): void {
  adapters.clear();
}
