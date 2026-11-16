/**
 * CreateCatalogItemModal — the ONE manual definition surface for catalog
 * items (plan: catalog definitions are created from the browse dialog).
 *
 * Opens from the catalog browse dialog's "New <type>" action (and from the
 * unmapped-biomarker affordance); hosts the registry form for the type
 * ({@link getCatalogForm}, `mode="create"`) and writes through the unified
 * generic create service — one contract for every type, no per-type
 * branching, no client-side slug/seed_key (identity is server-owned; ADR 0055
 * D8: the backend derives the biomarker slug from the name when the payload
 * omits it, so the dialog passes `serverOwnedSlug` — the form renders the
 * slug field disabled and its display-only derivation is stripped from the
 * wire; explicit slugs remain the admin workspace's door). After the create
 * returns it persists the draft's deferred side-data
 * exactly like the catalog workspace does: biomarker stratified reference
 * ranges are reconciled via `biomarkerService.syncReferenceRanges` and graph
 * links are written via `createLinksFor` with the type descriptor's
 * `edge_endpoint_type`.
 *
 * Partial failure is one-way: once the create succeeded the row EXISTS — the
 * dialog never calls the create service again for this draft. If the
 * side-data step throws, it reports the created row (so the caller's form
 * binds to it) with a distinct "saved, but ranges/links failed" toast and
 * closes. Per-link outcomes flow back through `onCreated(row, meta)`:
 * `meta.persistedLinks` carries only the links the dialog actually wrote, so
 * the HITL handlers retry exactly the failed ones at confirm time.
 *
 * The draft is re-initialized from `initialName` / `initialDraft` on every
 * open. Errors toast the backend's first readable message (the 422 array
 * `detail` renders as `field: msg`) and keep the dialog open; success
 * reports the created row (with its server-derived identity) through
 * `onCreated(row, meta)` and closes.
 *
 * Radix-portaled on purpose: it opens from inside other dialogs (the browse
 * modal, the record-form popup), so it owns its focus scope and Escape
 * handling and stays outside the caller's `<form>` in the DOM.
 */
import React, { useCallback, useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import { Modal } from '../ui/Modal';
import { AIAssistButton } from '../ui/AIAssistButton';
import { RecordFormFooter } from '../records/RecordFormFooter';
import { RecordFormIcon } from '../records/RecordFormIcon';
import { getCatalogForm } from './forms/catalogForms';
import { getCatalogTypeIcon } from './catalogTypeIcons';
import { toWritePayload } from '../../pages/Catalogs/catalogWritePayload';
import { createCatalogItem, listCatalogTypes } from '../../services/catalogService';
import biomarkerService from '../../services/biomarkerService';
import { apiErrorDetail } from '../../utils/apiErrors';
import {
  createLinksFor,
  selectionsToLinkInputs,
  type LinkCreateResult,
} from '../../services/conceptEdges';
import type {
  CatalogItem,
  CatalogSelection,
  CatalogTypeMeta,
} from '../../types/catalog';

/** What the dialog already persisted for the created row (S2): the caller's
 *  confirm step must re-persist exactly the links NOT in this set. */
export interface CreateCatalogItemMeta {
  /** Graph links successfully written on the created row (never a failed or
   *  skipped one — those stay the caller's job at confirm time). */
  persistedLinks: CatalogSelection[];
}

export interface CreateCatalogItemModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** The catalog type to define (biomarker | medication | allergy | vaccine). */
  type: string;
  /** Browse search text at click time — the draft's starting name. */
  initialName?: string;
  /** Extra definition fields for the draft (e.g. a HITL proposal's definition
   *  fields). Applied verbatim; identity/system fields are dropped by the
   *  payload allowlist. */
  initialDraft?: Partial<CatalogItem>;
  /** Links to persist on the created row in addition to `draft.links`. */
  initialLinks?: CatalogSelection[];
  /** Receives the created row (server slug / seed_key included) plus what the
   *  dialog already persisted on it. */
  onCreated: (row: CatalogItem, meta?: CreateCatalogItemMeta) => void;
}

/** Build the draft from the current initial values (re-run on every open). */
const buildDraft = (
  type: string,
  initialName?: string,
  initialDraft?: Partial<CatalogItem>,
  initialLinks?: CatalogSelection[],
): CatalogItem => {
  const draft: CatalogItem = {
    name: initialName ?? '',
    ...(initialDraft ?? {}),
  };
  const draftLinks = Array.isArray(draft.links)
    ? (draft.links as CatalogSelection[])
    : [];
  const links = [...draftLinks, ...(initialLinks ?? [])];
  if (links.length > 0) draft.links = links;
  if (type === 'biomarker' && draft.coding_system === undefined) {
    draft.coding_system = 'loinc';
  }
  return draft;
};

/** Zip the link step's per-link outcomes back onto the selections that were
 *  attempted: `createLinksFor` returns results in input order, and the inputs
 *  are the relation-bearing selections in order. */
const selectionsForResults = (
  selections: CatalogSelection[],
  results: LinkCreateResult[],
): CatalogSelection[] => {
  const attempted = selections.filter((s) => Boolean(s.relation));
  return attempted.filter((_, i) => results[i]?.ok);
};

export const CreateCatalogItemModal: React.FC<CreateCatalogItemModalProps> = ({
  isOpen,
  onClose,
  type,
  initialName = '',
  initialDraft,
  initialLinks,
  onCreated,
}) => {
  const { t } = useTranslation();
  const formId = useId();
  const [draft, setDraft] = useState<CatalogItem>(() =>
    buildDraft(type, initialName, initialDraft, initialLinks),
  );
  const [typeMeta, setTypeMeta] = useState<CatalogTypeMeta | null>(null);
  const [typesFailed, setTypesFailed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const openSeq = useRef(0);
  // The row this draft already created (S1): once set, the create service is
  // never called again — a failed side-data step must not duplicate the row.
  const createdRef = useRef<CatalogItem | null>(null);
  const reportedRef = useRef(false);

  // Reset on every open — the draft always reflects the latest initial
  // values (a stale draft must never survive a cancel/reopen cycle).
  useEffect(() => {
    if (!isOpen) return;
    const seq = ++openSeq.current;
    createdRef.current = null;
    reportedRef.current = false;
    setDraft(buildDraft(type, initialName, initialDraft, initialLinks));
    setSubmitting(false);
    setTypeMeta(null);
    setTypesFailed(false);
    (async () => {
      try {
        const resp = await listCatalogTypes();
        if (seq !== openSeq.current) return;
        setTypeMeta(resp.types.find((tg) => tg.type === type) ?? null);
      } catch {
        if (seq === openSeq.current) setTypesFailed(true);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, type]);

  const handleChange = useCallback(
    (patch: Record<string, unknown>) => setDraft((prev) => ({ ...prev, ...patch })),
    [],
  );

  const Form = getCatalogForm(type);

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      // Identity is server-owned (ADR 0055 D8): the backend derives the
      // biomarker slug from the name when the payload omits it, so the
      // dialog never sends one. `serverOwnedSlug` renders the form's slug
      // field disabled (its live derivation is display-only — an edit would
      // be silently dropped here); explicit slugs remain the admin
      // workspace's door.
      const payload = toWritePayload(type, draft, 'create', typeMeta?.write);
      delete payload.slug;
      const created = (await createCatalogItem(type, payload)) as CatalogItem;
      createdRef.current = created;
      const createdId = String(created.id ?? '');
      // Biomarker stratified ranges live outside the create payload (the
      // sync is their single writer — same reconciliation as the workspace).
      const ranges = Array.isArray(draft.reference_ranges)
        ? (draft.reference_ranges as never[])
        : [];
      if (createdId && ranges.length > 0) {
        await biomarkerService.syncReferenceRanges(createdId, ranges);
      }
      const draftLinks = Array.isArray(draft.links)
        ? (draft.links as CatalogSelection[])
        : [];
      const linkInputs = selectionsToLinkInputs(draftLinks);
      let linkResults: LinkCreateResult[] = [];
      if (createdId && typeMeta?.edge_endpoint_type && linkInputs.length > 0) {
        linkResults = await createLinksFor(
          typeMeta.edge_endpoint_type,
          createdId,
          linkInputs,
          { source: 'manual' },
        );
      }
      // Reported BEFORE invoking: a throwing caller must not fall into the
      // catch's re-report branch (a second onCreated for one row).
      reportedRef.current = true;
      onCreated(created, {
        persistedLinks: selectionsForResults(draftLinks, linkResults),
      });
      onClose();
    } catch (err) {
      if (createdRef.current && !reportedRef.current) {
        // The create succeeded but the side-data step threw (the ranges sync
        // is the one step that can) — the row EXISTS. Report it so the
        // caller's form binds to the new definition and close; retrying here
        // would duplicate the row (S1). Nothing after the create ran, so no
        // link counts as persisted — the caller's confirm re-persists them.
        onCreated(createdRef.current, { persistedLinks: [] });
        reportedRef.current = true;
        onClose();
        toast.error(
          t(
            'catalogs.created_side_data_failed',
            'Saved, but reference ranges or links could not be applied — reopen the item to retry.',
          ),
        );
        return;
      }
      toast.error(apiErrorDetail(err) ?? t('common.save_failed', 'Could not save'));
    } finally {
      setSubmitting(false);
    }
  };

  const hasName = Boolean(String(draft.name ?? '').trim());
  const canSubmit = hasName && !!typeMeta && !submitting;

  const typeLabel = t(
    `catalogs.${type}.label`,
    type.charAt(0).toUpperCase() + type.slice(1),
  );

  return (
    <Modal
      open={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title={t('catalogs.create_new_item', 'New {{type}}', { type: typeLabel })}
      description={t(
        'catalogs.create_new_hint',
        'Reusable definition — visible across the whole health record.',
      )}
      headerIcon={<RecordFormIcon icon={getCatalogTypeIcon(type)} tone="blue" />}
      headerActions={
        type === 'medication' ? (
          <AIAssistButton
            taskType="define_medication"
            context={{}}
            placeholder={t('catalogs.create_new_item', 'New {{type}}', {
              type: typeLabel,
            })}
            onSuggestedData={(data: Record<string, unknown>) =>
              setDraft((prev) => ({
                ...prev,
                name:
                  !String(prev.name ?? '').trim() && typeof data.name === 'string'
                    ? data.name
                    : prev.name,
                description: data.description ?? prev.description,
                indications: data.indications ?? prev.indications,
                dosage_info: data.dosage_info ?? prev.dosage_info,
                contraindications: data.contraindications ?? prev.contraindications,
                side_effects:
                  Array.isArray(data.side_effects) && data.side_effects.length > 0
                    ? data.side_effects
                    : prev.side_effects,
              }))
            }
          />
        ) : undefined
      }
      closeLabel={t('common.close')}
      footer={
        <RecordFormFooter
          formId={formId}
          canSubmit={canSubmit}
          loading={submitting}
          submitLabel={t('common.save', 'Save')}
          cancelLabel={t('common.cancel', 'Cancel')}
          onCancel={onClose}
        />
      }
      bodyFill
      bodyClassName="p-0"
      size="md"
    >
      <form
        id={formId}
        onSubmit={(event) => {
          event.preventDefault();
          void handleSubmit();
        }}
        className="flex min-h-0 flex-1 flex-col"
      >
        <div className="flex-1 min-h-0 overflow-y-auto p-6 custom-scrollbar">
          {typesFailed && (
            <div
              role="alert"
              className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:border-amber-500/30 dark:bg-amber-900/20 dark:text-amber-300"
            >
              {t(
                'catalogs.types_load_failed',
                'The form fields could not be loaded — Save is disabled. Close and reopen this dialog.',
              )}
            </div>
          )}
          <Form
            values={draft}
            onChange={handleChange}
            mode="create"
            serverOwnedSlug
          />
        </div>
      </form>
    </Modal>
  );
};

export default CreateCatalogItemModal;
