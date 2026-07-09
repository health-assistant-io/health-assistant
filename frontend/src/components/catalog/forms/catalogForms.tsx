/**
 * Per-type catalog item form registry (Phase D follow-up).
 *
 * Adding a form for a catalog type = one component + one entry in
 * `CATALOG_FORMS`. The browser's create/edit modal dispatches to the matching
 * form via `getCatalogForm(type)`, falling back to `GenericCatalogForm`
 * (name + description). Each form is controlled: it renders the type-specific
 * fields and calls `onChange(patch)` to update the draft held by the modal.
 */
import type { ComponentType } from 'react';
import type { CatalogItem, CatalogType, CatalogTypeMeta } from '../../../types/catalog';
import { GenericCatalogForm } from './GenericCatalogForm';
import { BiomarkerForm } from './BiomarkerForm';
import { MedicationForm } from './MedicationForm';
import { AllergyForm } from './AllergyForm';
import { VaccineForm } from './VaccineForm';
import { ConceptForm } from './ConceptForm';

export interface CatalogItemFormProps {
  typeMeta: CatalogTypeMeta;
  /** The current draft values (the item being created/edited). */
  values: CatalogItem;
  /** Push a partial update into the draft. */
  onChange: (patch: Record<string, unknown>) => void;
  /** 'create' hides readonly/identity fields (slug auto-derived etc.). */
  mode: 'create' | 'edit';
}

export interface CatalogFormConfig {
  types: CatalogType[];
  Component: ComponentType<CatalogItemFormProps>;
}

/** The registry — append here to add a type-specific form. */
export const CATALOG_FORMS: CatalogFormConfig[] = [
  { types: ['biomarker'], Component: BiomarkerForm },
  { types: ['medication'], Component: MedicationForm },
  { types: ['allergy'], Component: AllergyForm },
  { types: ['vaccine'], Component: VaccineForm },
  { types: ['concept'], Component: ConceptForm },
];

/** Resolve the form for a catalog type (falls back to the generic form). */
export function getCatalogForm(type: string): ComponentType<CatalogItemFormProps> {
  const entry = CATALOG_FORMS.find((f) => f.types.includes(type as CatalogType));
  return entry ? entry.Component : GenericCatalogForm;
}
