/**
 * Setup wizard types — mirror the backend `SetupChecklistResponse` +
 * `ExtensionCatalogResponse` from `GET /api/v1/setup/*`.
 *
 * The wizard UI is kind-driven (design D7): each `SetupStep.kind` has one
 * renderer component. The `completed` bit is authoritative (backend-derived
 * from live data, design D2 — no onboarding-state table).
 *
 * See `dev/audits/setup-wizard-design.md` and `docs/SETUP_WIZARD.md`.
 */

export type SetupStepKind = 'redirect' | 'inline_form' | 'external_config' | 'derived';

/** A guided sub-step for external_config steps (e.g. AI config provider→model→tasks). */
export interface GuidedSubStep {
  id: string;
  done: boolean;
  route: string;
}

/** Freeform UI metadata. */
export interface StepPayloadHint {
  route?: string;
  /** Guided sub-step list for external_config steps that open the real settings page. */
  sub_steps?: GuidedSubStep[];
}

export interface SetupStep {
  id: string;
  /** Entity scope, e.g. 'patient'; null for role steps. */
  entity?: string | null;
  title_i18n_key: string;
  kind: SetupStepKind;
  /** Authoritative effective state — evaluator-completed OR manually-completed. */
  completed: boolean;
  /** True only when completion is via a manual user override (not the
   * evaluator). The UI uses this to render an "undo" affordance + a
   * "marked manually" hint. Stays false when the evaluator already agreed.
   * Optional on the client for legacy mock fixtures; the backend always
   * sends it. */
  manually_completed?: boolean;
  /** Optional steps do not count toward `completion`. */
  optional: boolean;
  /** Freeform UI metadata. */
  payload_hint?: StepPayloadHint | null;
}

export interface SetupChecklist {
  role: string;
  entity?: string | null;
  entity_id?: string | null;
  steps: SetupStep[];
  /** Mandatory-only progress ratio, 0.0–1.0. */
  completion: number;
}

/** A coded picklist option for an extension value (e.g. an OMB race code). */
export interface ExtensionOption {
  code: string;
  display: string;
}

/**
 * One supported patient extension, surfaced via the extension-catalog
 * endpoint so the client renders the correct input without hardcoding
 * keys or CDC code lists.
 *
 * - `omb_category` → dropdown of `options`; written back as
 *   `{ ombCategory: { code, display }, text }`.
 * - `code` → dropdown of `options` (e.g. preferred_language).
 * - `string` → free-text input.
 */
export interface ExtensionFieldSpec {
  key: string;
  title_i18n_key: string;
  value_type: 'omb_category' | 'code' | 'string';
  cardinality: string;
  options?: ExtensionOption[] | null;
}

export interface ExtensionCatalog {
  entity: string;
  extensions: ExtensionFieldSpec[];
}
