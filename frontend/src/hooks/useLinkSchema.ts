/**
 * useLinkSchema — discovery hook for the link-schema matrix.
 *
 * Wraps {@link getLinkSchema} with a module-level cache so the first mount of
 * any `<LinksSection>` pays the network cost; subsequent mounts (across
 * different forms in the same session) read from the cache synchronously.
 *
 * The matrix is pure metadata (no DB hit on the server, no per-tenant
 * variation), so it's safe to share across users/tenants for the app lifetime.
 *
 * Two access modes:
 *  - `useLinkSchema()`              → the full matrix as `LinkSchemaRow[]`
 *  - `useLinkSchema('medication')`  → filtered to one source, keyed by
 *                                      destination type (`LinkSchemaForSource`)
 *
 * The filtered view is derived client-side from the cached full matrix, so
 * asking for a new `srcType` doesn't re-fetch.
 */
import { useEffect, useState } from 'react';
import * as conceptEdges from '../services/conceptEdges';
import type {
  LinkSchemaForSource,
  LinkSchemaRow,
} from '../services/conceptEdges';

// ---------------------------------------------------------------------------
// Module-level cache — shared across every consumer in the session.
// Settled once on first fetch; reused on every subsequent mount. Errors
// clear the promise so a re-mount retries.
// ---------------------------------------------------------------------------

let _fullCache: LinkSchemaRow[] | null = null;
let _fullPromise: Promise<LinkSchemaRow[]> | null = null;

async function _loadFull(): Promise<LinkSchemaRow[]> {
  if (_fullCache) return _fullCache;
  if (_fullPromise) return _fullPromise;
  _fullPromise = (async () => {
    // Call through the namespace so unit-test spies on
    // `conceptEdges.getLinkSchema` intercept this call too (named-import
    // bindings are frozen at module-eval time in some bundlers).
    const rows = await conceptEdges.getLinkSchema();
    _fullCache = rows;
    return rows;
  })().catch((err) => {
    // Allow the next mount to retry.
    _fullPromise = null;
    throw err;
  });
  return _fullPromise;
}

/** Test-only: reset the cache between unit tests. */
export function _resetLinkSchemaCacheForTests(): void {
  _fullCache = null;
  _fullPromise = null;
}

/** Project the cached full matrix into a per-source view. Pure helper exposed
 *  so consumers (and tests) can derive it without re-fetching. */
export function deriveSchemaForSource(
  rows: LinkSchemaRow[],
  srcType: string,
): LinkSchemaForSource {
  const out: LinkSchemaForSource = {};
  for (const row of rows) {
    if (row.src_type === srcType) {
      out[row.dst_type] = row.relations;
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export interface UseLinkSchemaResult<T> {
  schema: T | null;
  loading: boolean;
  error: string | null;
}

export function useLinkSchema(): UseLinkSchemaResult<LinkSchemaRow[]>;
export function useLinkSchema(srcType: string): UseLinkSchemaResult<LinkSchemaForSource>;
export function useLinkSchema<T extends LinkSchemaRow[] | LinkSchemaForSource>(
  srcType?: string,
): UseLinkSchemaResult<T> {
  const [schema, setSchema] = useState<T | null>(
    () => (_fullCache ? (_project(_fullCache, srcType) as T) : null),
  );
  const [loading, setLoading] = useState(!_fullCache);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (!_fullCache && !_fullPromise) {
      setLoading(true);
    }
    _loadFull()
      .then((rows) => {
        if (cancelled) return;
        setSchema(_project(rows, srcType) as T);
        setLoading(false);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [srcType]);

  return { schema, loading, error };
}

function _project(
  rows: LinkSchemaRow[],
  srcType: string | undefined,
): LinkSchemaRow[] | LinkSchemaForSource {
  if (srcType === undefined) return rows;
  return deriveSchemaForSource(rows, srcType);
}
