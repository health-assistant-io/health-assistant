/**
 * Core types for the Unified Instance Picker — the patient-scoped-records
 * counterpart of the catalog (definition) picker.
 *
 * Design principle (mirrors the catalog side): the generic UI components
 * (`InstanceBrowser`, `InstancePicker`, `InstanceBrowseModal`) NEVER touch an
 * entity's raw shape. Each entity type ships an `InstanceAdapter<T>` that
 * projects records into the uniform {@link InstanceRow} shape and declares how
 * to fetch/search/filter them. This keeps the components domain-agnostic and
 * the per-entity code isolated in adapter modules
 * (`features/instances/adapters/*`).
 *
 * See `dev/plans/instance-browser-unified-picker-2026-07-16.md`.
 */
import type { FacetDefinition } from '../ui/filters/types';
import type React from 'react';

/**
 * The patient-scoped entity types the picker serves. Mirrors the catalog
 * `CatalogType` set but for *instances* (actual patient records), not
 * definitions. Add a value here + register an adapter to extend the picker.
 */
export type InstanceType =
  | 'examination'
  | 'medication'
  | 'observation'
  | 'document'
  | 'event'
  | 'allergy'
  | 'vaccine';

/**
 * A uniform row projection so the generic browser never touches entity shape.
 * An adapter's `toRow(item)` produces this. Every field the browser needs to
 * render (label, subtitle, date, status, badges, icon) lives here — the
 * browser does no entity-specific field access.
 */
export interface InstanceRow {
  /** Stable unique id within the entity type (the record's UUID). */
  id: string;
  /** The entity type this row belongs to. */
  type: InstanceType;
  /** Primary display text (e.g. exam category, medication name). */
  label: string;
  /**
   * Secondary text (e.g. date + notes snippet, dosage, MRN). **Must be a
   * single plain-text line** — adapters strip HTML/Markdown via `toSnippet`
   * before storing it here, because this field is rendered as-is in compact
   * one-line contexts (the card, the browse list row, the preview header).
   * Rich multi-line content belongs in {@link InstanceRow.description}.
   */
  subtitle?: string;
  /**
   * Optional rich-text body for the preview pane (may be HTML from the Quill
   * editor, Markdown from AI/import, or plain text). Rendered via
   * `FormattedText` (auto-detects format). Adapters populate this from the
   * entity's main free-text field (exam notes, event description, etc.).
   */
  description?: string;
  /** ISO date string rendered as a relative timestamp; usually the record date. */
  date?: string;
  /** Status label (e.g. 'active', 'final', 'resolved', 'completed'). */
  status?: string;
  /** Optional color for the status badge (hex or tailwind text color class). */
  statusColor?: string | null;
  /** Lucide icon name (resolved by the browser via DynamicIcon). */
  icon?: string;
  /** Extra categorical chips (e.g. category, coding system). */
  badges?: { label: string; color?: string | null }[];
  /**
   * The original entity object, kept so client-mode facet predicates (which
   * are typed against the real entity `T`) can run over the loaded items. The
   * browser itself never reads this; the adapter's facets do.
   */
  raw: unknown;
}

/**
 * Controlled selection emitted by the picker. Always an array (length ≤ 1 in
 * `single` mode). Mirrors `CatalogSelection` so the two pickers feel
 * symmetrical to form authors.
 *
 * `label`/`subtitle` are cached at pick time so chips render without a second
 * fetch even if the record isn't in any loaded list.
 */
export interface InstanceSelection {
  type: InstanceType;
  id: string;
  label?: string;
  subtitle?: string;
  /** Relation-type code when `relationPicker` is enabled (mirrors catalog). */
  relation?: string;
}

/**
 * A single hit from a free-text instance search (the inline type-ahead). The
 * adapter's `search()` returns these; the picker renders them as a flat
 * result list tagged with their entity type.
 */
export interface InstanceSearchHit {
  type: InstanceType;
  id: string;
  label: string;
  subtitle?: string;
  date?: string;
}

/** Paginated fetch result returned by an adapter's `fetch()`. */
export interface InstanceFetchResult<T> {
  items: T[];
  /** True total in the (filtered) collection, or `items.length` when the
   *  backend does not paginate (graceful degradation — "Load more" is hidden). */
  total: number;
}

/**
 * Query passed to an adapter's `fetch`/`search`. Patient scoping is the
 * default and mandatory for most entity types; a picker binds the current
 * patient context unless a caller explicitly passes a different `patientId`.
 */
export interface InstanceQuery {
  /** Patient scope. Defaults to the current patient context at the picker. */
  patientId?: string;
  /** Free-text query (may be applied client- or server-side by the adapter). */
  q?: string;
  limit: number;
  offset: number;
  /** Server-mode facet params, serialized by `useFilterState.serverParams`. */
  serverParams: Record<string, string>;
}

/**
 * Per-entity adapter. Generic in `T` (the entity record type) so facet
 * predicates and row projection are fully type-safe. This is the instance
 * counterpart of the catalog side's registry glue
 * (`writeTarget.ts` + `catalogFacetRegistry.ts`) combined into one interface.
 *
 * Register via {@link registerAdapter}; resolve via {@link getAdapter}.
 */
export interface InstanceAdapter<T> {
  type: InstanceType;
  /** Display labels (singular/plural) + optional i18n key override. */
  entityLabel: { singular: string; plural: string; i18nKey?: string };
  /** Default lucide icon name for this entity type. */
  icon: string;

  /**
   * May the picker browse this type tenant-wide (no `patientId`)? Default
   * false = patient-scoped only (the secure default). When false, the modal
   * refuses tenant-wide browsing even if the backend would allow it for admins.
   */
  allowTenantScope?: boolean;

  /**
   * List records (with optional search + server facets + pagination). The
   * adapter is responsible for normalizing its service's return shape into
   * {@link InstanceFetchResult} (e.g. `total = items.length` when the backend
   * doesn't paginate yet — see plan §Phase 3 normalization note).
   */
  fetch(query: InstanceQuery): Promise<InstanceFetchResult<T>>;

  /**
   * Free-text search for the inline type-ahead. Defaults to `fetch` + a
   * client-side `toRow` projection when not overridden. Override to call the
   * unified `/instances/search` dispatcher (Phase 3) for efficiency.
   */
  search?(query: InstanceQuery): Promise<InstanceSearchHit[]>;

  /**
   * Fetch a single record by id (used by {@link InstanceCard} to render the
   * basic-info card for a selection). Delegates to the domain getById service;
   * `patientId` is supplied for adapters whose only lookup is patient-scoped
   * (e.g. allergies, which have no getById endpoint and resolve from the list).
   * Returns the raw entity — callers project via {@link InstanceAdapter.toRow}.
   */
  fetchOne(id: string, patientId?: string): Promise<T>;

  /** Facets for this entity (client- and/or server-mode). Fed to FilterBar. */
  facets: FacetDefinition<T>[];

  /** Project an entity record into the uniform row shape for the browser. */
  toRow(item: T): InstanceRow;
  /** Project an entity record into a selection (for chip rendering). */
  toSelection(item: T): InstanceSelection;
  /** Domain detail route for "Open in …" (mirrors catalog domainRoute). */
  detailRoute(item: T): string | null;

  /**
   * Optional **per-type browse view**. When present, the browse modal renders
   * this component instead of the generic `InstanceBrowser` + `InstancePreview`
   * — so each entity type can reuse its purpose-built UI (e.g. examinations
   * render the robust `ExaminationCard` list + `ExaminationPreview`; biomarker
   * observations render the trends card grid). When absent, the generic
   * uniform view is used. The view owns its own layout (master-detail, grid,
   * …) and receives the already-fetched/filtered items + pick state.
   */
  View?: React.ComponentType<InstanceViewProps<T>>;
}

/**
 * Props passed to an adapter's per-type {@link InstanceAdapter.View}. The modal
 * supplies the data (already q-filtered by the adapter's `fetch`) + pick state;
 * the view renders the type-specific layout.
 */
export interface InstanceViewProps<T> {
  /** Fetched (+ q-filtered) entity records for the active type. */
  items: T[];
  /** ids already picked (highlight/toggle state). */
  pickedIds: string[];
  /** Toggle one record into/out of the selection. */
  onTogglePick: (item: T) => void;
  /** Patient scope (passed through from the modal). */
  patientId?: string;
  loading: boolean;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
}
