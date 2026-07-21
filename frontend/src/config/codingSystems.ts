/**
 * Coding systems — the single source of truth for clinical terminology
 * systems across the app (LOINC, SNOMED CT, CVX, ICD-10, ATC, RxNorm, MeSH,
 * FMA, custom).
 *
 * Replaces the per-form hardcoded `<option>` lists + local `CODING_SYSTEMS`
 * arrays that had drifted (BiomarkerForm/VaccineForm/ClinicalEventForm used
 * inline options; AnatomyForm + ConceptForm each declared their own local
 * `CODING_SYSTEMS` constant). Every form now reads from this registry via
 * {@link CodingSystemSelect}.
 *
 * `fhirSystem` is the canonical FHIR `system` URL for each code system
 * (mirrors `backend/app/models/enums.py:CodingSystem.fhir_system`, extended
 * to the wider set vaccines/concepts/anatomy use). Kept here so the CodeBadge,
 * FHIR export, and the selector never drift on the URL either.
 */

export interface CodingSystemDef {
  /** Stored value (lowercase key, e.g. `'loinc'`, `'cvx'`). */
  value: string;
  /** Human label shown in the dropdown. */
  label: string;
  /** Canonical FHIR `system` URL. */
  fhirSystem: string;
  /** Short description (shown as the option `title` tooltip). */
  hint?: string;
}

export const CODING_SYSTEMS: CodingSystemDef[] = [
  {
    value: 'loinc',
    label: 'LOINC',
    fhirSystem: 'http://loinc.org',
    hint: 'Lab tests & clinical observations',
  },
  {
    value: 'snomed',
    label: 'SNOMED CT',
    fhirSystem: 'http://snomed.info/sct',
    hint: 'Clinical terminology',
  },
  {
    value: 'cvx',
    label: 'CVX',
    fhirSystem: 'http://hl7.org/fhir/sid/cvx',
    hint: 'Vaccine codes (HL7)',
  },
  {
    value: 'icd10',
    label: 'ICD-10',
    fhirSystem: 'http://hl7.org/fhir/sid/icd-10',
    hint: 'Disease / diagnosis classification',
  },
  {
    value: 'atc',
    label: 'ATC',
    fhirSystem: 'https://www.whocc.no/atc/',
    hint: 'Anatomical Therapeutic Chemical (drug classes)',
  },
  {
    value: 'rxnorm',
    label: 'RxNorm',
    fhirSystem: 'http://www.nlm.nih.gov/research/umls/rxnorm',
    hint: 'Clinical drugs',
  },
  {
    value: 'mesh',
    label: 'MeSH',
    fhirSystem: 'https://id.nlm.nih.gov/mesh',
    hint: 'Medical Subject Headings',
  },
  {
    value: 'fma',
    label: 'FMA',
    fhirSystem: 'http://purl.obolibrary.org/obo/fma',
    hint: 'Foundational Model of Anatomy',
  },
  {
    value: 'custom',
    label: 'Custom',
    fhirSystem: 'urn:uuid:health-assistant:custom',
    hint: 'User-defined code system',
  },
];

/**
 * The clinical domains that select a coding system. Each maps to the subset
 * of {@link CODING_SYSTEMS} relevant for that entity. Add a domain here (and
 * its value list) to extend — the selector picks it up automatically.
 */
export type CodingDomain =
  | 'biomarker'
  | 'vaccine'
  | 'clinical_event'
  | 'medication'
  | 'anatomy'
  | 'concept';

const DOMAIN_VALUES: Record<CodingDomain, string[]> = {
  biomarker: ['loinc', 'snomed', 'custom'],
  vaccine: ['cvx', 'snomed', 'custom'],
  clinical_event: ['loinc', 'snomed', 'custom'],
  medication: ['atc', 'rxnorm', 'snomed', 'custom'],
  anatomy: ['snomed', 'fma', 'custom'],
  // Concepts span every domain, so offer the full set.
  concept: CODING_SYSTEMS.map((s) => s.value),
};

const CODING_SYSTEMS_BY_VALUE = new Map(
  CODING_SYSTEMS.map((s) => [s.value, s]),
);

/** Resolve domain subsets once (single source = the master array above). */
export const CODING_SYSTEMS_BY_DOMAIN: Record<
  CodingDomain,
  CodingSystemDef[]
> = Object.fromEntries(
  Object.entries(DOMAIN_VALUES).map(([domain, values]) => [
    domain,
    values
      .map((v) => CODING_SYSTEMS_BY_VALUE.get(v))
      .filter((s): s is CodingSystemDef => Boolean(s)),
  ]),
) as Record<CodingDomain, CodingSystemDef[]>;

/** Look up a system definition by its stored value. */
export function getCodingSystem(
  value: string | null | undefined,
): CodingSystemDef | undefined {
  return value ? CODING_SYSTEMS_BY_VALUE.get(value) : undefined;
}

/** The systems available for a domain (falls back to all). */
export function getCodingSystemsForDomain(
  domain: CodingDomain,
): CodingSystemDef[] {
  return CODING_SYSTEMS_BY_DOMAIN[domain] ?? CODING_SYSTEMS;
}

/** Display label for a value (`'LOINC'`), falling back to uppercased value. */
export function getCodingSystemLabel(
  value: string | null | undefined,
): string {
  const def = getCodingSystem(value);
  return def ? def.label : value ? value.toUpperCase() : '—';
}
