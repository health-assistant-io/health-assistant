/**
 * SuggestedDefinitionChip — the HITL "AI suggested a new item" affordance
 * (unmatched proposals create definitions through the same dialog).
 *
 * Rendered above the record form when the proposal is unmatched (`is_new` /
 * `matched: false`). Opens {@link CreateCatalogItemModal} — the ONE manual
 * definition surface — prefilled with the proposal's DEFINITION fields only
 * (the handler owns the whitelist: patient-instance text such as reason,
 * note, dosage or dates never reaches the catalog draft) plus the proposed
 * graph links, which the dialog persists on the created definition at create
 * time. The created row is reported back through `onCreated` so the handler
 * can set the form's `catalogSelection`.
 *
 * Persistent by design: cancelling the dialog (or its hosting popup cycle)
 * keeps the chip; only a successful create (or resolving the proposal)
 * retires it.
 */
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Sparkles } from 'lucide-react';
import { CreateCatalogItemModal } from '../../catalog/CreateCatalogItemModal';
import { getCatalogTypeIcon } from '../../catalog/catalogTypeIcons';
import type {
  CatalogItem,
  CatalogSelection,
} from '../../../types/catalog';
import type { CreateCatalogItemMeta } from '../../catalog/CreateCatalogItemModal';

export interface SuggestedDefinitionChipProps {
  /** Catalog type to define (medication | allergy | biomarker). */
  type: string;
  /** The proposed name — chip label and the dialog's starting name. */
  name?: string;
  /** Definition fields only (no patient-instance text). */
  initialDraft?: Partial<CatalogItem>;
  /** Proposed graph links — persisted on the created definition. */
  initialLinks?: CatalogSelection[];
  /** Receives the created row (server-derived slug / seed_key included) plus
   *  what the dialog already persisted on it (so only FAILED links are
   *  retried at confirm time). */
  onCreated: (row: CatalogItem, meta?: CreateCatalogItemMeta) => void;
}

export const SuggestedDefinitionChip: React.FC<SuggestedDefinitionChipProps> = ({
  type,
  name,
  initialDraft,
  initialLinks,
  onCreated,
}) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const typeLabel = t(
    `catalogs.${type}.label`,
    type.charAt(0).toUpperCase() + type.slice(1),
  );
  const TypeIcon = getCatalogTypeIcon(type);

  return (
    <>
      <div className="mx-4 mt-4">
        <button
          type="button"
          data-testid="suggested-definition-chip"
          onClick={() => setOpen(true)}
          className="inline-flex w-full items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-left text-[11px] font-semibold text-amber-700 transition-colors hover:bg-amber-100 dark:border-amber-500/30 dark:bg-amber-900/20 dark:text-amber-300 dark:hover:bg-amber-900/30"
        >
          <Sparkles className="h-3.5 w-3.5 shrink-0 text-amber-500 dark:text-amber-400" />
          <TypeIcon className="h-3.5 w-3.5 shrink-0 text-amber-500 dark:text-amber-400" />
          <span className="min-w-0 flex-1 truncate">
            {t('ai_chat.hitl.define_suggested_item', 'AI suggested a new {{type}} "{{name}}" — Define it', { type: typeLabel, name: name ?? '' })}
          </span>
        </button>
      </div>
      <CreateCatalogItemModal
        isOpen={open}
        onClose={() => setOpen(false)}
        type={type}
        initialName={name}
        initialDraft={initialDraft}
        initialLinks={initialLinks}
        onCreated={onCreated}
      />
    </>
  );
};

export default SuggestedDefinitionChip;
