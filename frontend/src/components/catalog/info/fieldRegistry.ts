/**
 * fieldRegistry — the declarative source of truth for how each catalog
 * type's fields are laid out in the Info tab.
 *
 * Each {@link FieldDescriptor} maps one item key to a renderer (`kind`) + the
 * section it belongs to. The orchestrator ({@link CatalogItemInfo}) reads the
 * registry via {@link useFieldDescriptors}, groups descriptors into sections,
 * and renders them. Keys present on an item but not in its type's registry
 * land in the trailing "Additional fields" catch-all section, so no data is
 * ever silently hidden.
 *
 * Phase 2 scope: `kv` (label/value grid) + `richtext` (formatted prose) only.
 * The discriminated union grows in Phase 3 to add `code` / `chips` / `boolean`
 * / `enum` / `refranges` / `dose` / `object` kinds, at which point the
 * orchestrator's switch gains one case per kind (exhaustiveness-enforced).
 *
 * Section placement follows the Phase 0 payload findings (see plan §14):
 * biomarker exposes `preferred_unit_symbol` (expanded) + `reference_ranges[]`;
 * concept exposes `kinds`/`primary_kind`/`parent_slug`; etc.
 */
import {
  Hash,
  Code2,
  FileText,
  ShieldAlert,
  Zap,
  CalendarClock,
  Target,
  Palette,
  Tags,
  Map as MapIcon,
  Ruler,
  Gauge,
  MoreHorizontal,
  Braces,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { CatalogType } from '../../../types/catalog';
import type { ChipVariant } from '../../ui/ChipList';

/** The logical group a field renders under. Order is fixed by SECTION_ORDER. */
export type SectionId =
  | 'identity'
  | 'coding'
  | 'clinical'
  | 'safety'
  | 'reactions'
  | 'schedule'
  | 'targets'
  | 'presentation'
  | 'aliases'
  | 'display'
  | 'unit'
  | 'reference_ranges'
  | 'additional'
  | 'meta';

interface SectionMeta {
  labelKey: string;
  labelFallback: string;
  icon: LucideIcon;
}

/** Display metadata per section (label + leading icon). */
export const SECTION_META: Record<SectionId, SectionMeta> = {
  identity: { labelKey: 'catalogs.section_identity', labelFallback: 'Identity', icon: Hash },
  coding: { labelKey: 'catalogs.section_coding', labelFallback: 'Coding', icon: Code2 },
  clinical: { labelKey: 'catalogs.section_clinical', labelFallback: 'Clinical', icon: FileText },
  safety: { labelKey: 'catalogs.section_safety', labelFallback: 'Safety', icon: ShieldAlert },
  reactions: { labelKey: 'catalogs.section_reactions', labelFallback: 'Reactions', icon: Zap },
  schedule: { labelKey: 'catalogs.section_schedule', labelFallback: 'Schedule', icon: CalendarClock },
  targets: { labelKey: 'catalogs.section_targets', labelFallback: 'Targets', icon: Target },
  presentation: { labelKey: 'catalogs.section_presentation', labelFallback: 'Presentation', icon: Palette },
  aliases: { labelKey: 'catalogs.section_aliases', labelFallback: 'Aliases', icon: Tags },
  display: { labelKey: 'catalogs.section_display', labelFallback: 'Display', icon: MapIcon },
  unit: { labelKey: 'catalogs.section_unit', labelFallback: 'Unit', icon: Ruler },
  reference_ranges: { labelKey: 'catalogs.section_reference_ranges', labelFallback: 'Reference ranges', icon: Gauge },
  additional: { labelKey: 'catalogs.section_additional', labelFallback: 'Additional fields', icon: MoreHorizontal },
  meta: { labelKey: 'catalogs.section_meta', labelFallback: 'Meta', icon: Braces },
};

/** Render order for sections (others omitted if empty). */
export const SECTION_ORDER: SectionId[] = [
  'identity',
  'coding',
  'reference_ranges',
  'unit',
  'schedule',
  'targets',
  'clinical',
  'safety',
  'reactions',
  'presentation',
  'aliases',
  'display',
  'meta',
  'additional',
];

/** Fields that are never shown in the body (handled by the header / footer /
 *  derived). Mirrors the pre-refactor `META_KEYS` so Phase 2 output matches. */
export const META_KEYS: ReadonlySet<string> = new Set([
  'id',
  'tenant_id',
  'created_by',
  'updated_by',
  'created_at',
  'updated_at',
  'scope',
  'version',
  'is_custom',
]);

/** Base shape every descriptor shares. */
interface FieldBase {
  /** The item key this descriptor reads. */
  key: string;
  /** i18n key (resolved by the orchestrator). */
  labelKey: string;
  /** Default label when the i18n key is missing. */
  labelFallback: string;
  section: SectionId;
  /** Hide when the value is null/empty/[] (default true). */
  hideWhenEmpty?: boolean;
}

/**
 * Discriminated union of field renderers. The default variant (no `kind`, or
 * `kind: undefined`) is the key/value grid; other kinds carry the discriminant
 * and their own typed props. This keeps registry entries concise (kv fields
 * omit `kind`) while staying type-safe: TS narrows on `d.kind`. An unhandled
 * `kind` degrades safely to the kv grid (the orchestrator's `default` case).
 */
export type FieldDescriptor =
  | (FieldBase & {
      kind?: undefined;
      /** Render the value in a monospace font (codes, ids, slugs). */
      mono?: boolean;
      /** Append a copy affordance copying the raw value. */
      copyable?: boolean;
    })
  | (FieldBase & { kind: 'richtext' })
  | (FieldBase & {
      /** CodeBadge: code + system badge + external lookup + copy. The system
       *  value is read from `item[systemKey]` (a sibling field). */
      kind: 'code';
      systemKey?: string;
    })
  | (FieldBase & {
      /** ChipList: string[] as semantic pills. */
      kind: 'chips';
      variant: ChipVariant;
    })
  | (FieldBase & {
      /** BooleanPill: on/off flag indicator. */
      kind: 'boolean';
      labelOn: string;
      labelOff: string;
    })
  | (FieldBase & {
      /** EnumBadgeField: closed-set value resolved via `options` (raw→label),
       *  optionally colored per value via `tones`. */
      kind: 'enum';
      options: Record<string, string>;
      tones?: Partial<Record<string, ChipVariant>>;
    })
  | (FieldBase & {
      /** RefRangesField: biomarker stratified reference_ranges table. */
      kind: 'refranges';
    })
  | (FieldBase & {
      /** DoseScheduleField: vaccine {doses, intervals[]} schedule. */
      kind: 'dose';
    })
  | (FieldBase & {
      /** ColorSwatchField: CSS/hex color string as a filled swatch + value. */
      kind: 'color';
    })
  | (FieldBase & {
      /** IconPreviewField: `{type, value}` icon descriptor rendered as a glyph.
       *  `colorKey` optionally tints the glyph from a sibling field (e.g. the
       *  concept's `color`). */
      kind: 'icon';
      colorKey?: string;
    });

// ---------------------------------------------------------------------------
// Per-type field registries.
//
// Fields not listed here are auto-collected into the "additional" section by
// useFieldDescriptors, so the lists below only need to cover the fields we want
// to place in a named section. Order within a type is preserved.
// ---------------------------------------------------------------------------

const BIOMARKER_FIELDS: FieldDescriptor[] = [
  { key: 'name', labelKey: 'catalogs.field_name', labelFallback: 'Name', section: 'identity' },
  { key: 'slug', labelKey: 'catalogs.field_slug', labelFallback: 'Slug', section: 'identity', mono: true, copyable: true },
  { key: 'aliases', labelKey: 'catalogs.field_aliases', labelFallback: 'Aliases', section: 'identity', kind: 'chips', variant: 'neutral' },
  { key: 'category', labelKey: 'catalogs.field_category', labelFallback: 'Category', section: 'identity' },
  { key: 'class_concept_name', labelKey: 'catalogs.taxonomy_link', labelFallback: 'Class', section: 'identity' },
  {
    key: 'code', labelKey: 'catalogs.field_code', labelFallback: 'Code', section: 'coding',
    kind: 'code', systemKey: 'coding_system',
  },
  { key: 'reference_range_min', labelKey: 'catalogs.col_min', labelFallback: 'Min', section: 'reference_ranges' },
  { key: 'reference_range_max', labelKey: 'catalogs.col_max', labelFallback: 'Max', section: 'reference_ranges' },
  {
    key: 'reference_ranges', labelKey: 'catalogs.field_reference_ranges', labelFallback: 'Stratified ranges',
    section: 'reference_ranges', kind: 'refranges', hideWhenEmpty: false,
  },
  { key: 'preferred_unit_symbol', labelKey: 'catalogs.col_unit', labelFallback: 'Preferred unit', section: 'unit' },
  { key: 'preferred_unit_id', labelKey: 'catalogs.field_unit_id', labelFallback: 'Unit ID', section: 'unit', mono: true, copyable: true, hideWhenEmpty: false },
  { key: 'info', labelKey: 'catalogs.field_info', labelFallback: 'Info', section: 'clinical', kind: 'richtext' },
  {
    key: 'is_telemetry', labelKey: 'catalogs.field_is_telemetry', labelFallback: 'Telemetry', section: 'meta',
    kind: 'boolean', labelOn: 'Telemetry', labelOff: 'Not telemetry',
  },
  { key: 'meta_data', labelKey: 'catalogs.field_meta_data', labelFallback: 'Metadata', section: 'meta', hideWhenEmpty: false },
];

const MEDICATION_FIELDS: FieldDescriptor[] = [
  { key: 'name', labelKey: 'catalogs.field_name', labelFallback: 'Name', section: 'identity' },
  { key: 'class_concept_name', labelKey: 'catalogs.taxonomy_link', labelFallback: 'Class', section: 'identity' },
  { key: 'description', labelKey: 'catalogs.field_description', labelFallback: 'Description', section: 'clinical', kind: 'richtext' },
  { key: 'indications', labelKey: 'catalogs.field_indications', labelFallback: 'Indications', section: 'clinical', kind: 'richtext' },
  { key: 'dosage_info', labelKey: 'catalogs.field_dosage', labelFallback: 'Dosage', section: 'clinical', kind: 'richtext' },
  { key: 'contraindications', labelKey: 'catalogs.field_contraindications', labelFallback: 'Contraindications', section: 'clinical', kind: 'richtext' },
  {
    key: 'side_effects', labelKey: 'catalogs.field_side_effects', labelFallback: 'Side effects',
    section: 'safety', kind: 'chips', variant: 'warning',
  },
];

const ALLERGY_CATEGORY_OPTIONS: Record<string, string> = {
  FOOD: 'Food',
  MEDICATION: 'Medication',
  ENVIRONMENT: 'Environment',
  BIOLOGIC: 'Biologic',
  OTHER: 'Other',
};

const ALLERGY_FIELDS: FieldDescriptor[] = [
  { key: 'name', labelKey: 'catalogs.field_name', labelFallback: 'Name', section: 'identity' },
  {
    key: 'category', labelKey: 'catalogs.field_category', labelFallback: 'Category', section: 'identity',
    kind: 'enum', options: ALLERGY_CATEGORY_OPTIONS,
  },
  { key: 'description', labelKey: 'catalogs.field_description', labelFallback: 'Description', section: 'clinical', kind: 'richtext' },
  {
    key: 'typical_reactions', labelKey: 'catalogs.field_typical_reactions', labelFallback: 'Typical reactions',
    section: 'reactions', kind: 'chips', variant: 'danger',
  },
];

const ANATOMY_FIELDS: FieldDescriptor[] = [
  { key: 'name', labelKey: 'catalogs.field_name', labelFallback: 'Name', section: 'identity' },
  { key: 'slug', labelKey: 'catalogs.field_slug', labelFallback: 'Slug', section: 'identity', mono: true, copyable: true },
  {
    key: 'standard_code', labelKey: 'catalogs.field_code', labelFallback: 'Code', section: 'coding',
    kind: 'code', systemKey: 'standard_system',
  },
  { key: 'description', labelKey: 'catalogs.field_description', labelFallback: 'Description', section: 'clinical', kind: 'richtext' },
  { key: 'display', labelKey: 'catalogs.field_display', labelFallback: 'Display', section: 'display', hideWhenEmpty: false },
];

const VACCINE_FIELDS: FieldDescriptor[] = [
  { key: 'name', labelKey: 'catalogs.field_name', labelFallback: 'Name', section: 'identity' },
  { key: 'slug', labelKey: 'catalogs.field_slug', labelFallback: 'Slug', section: 'identity', mono: true, copyable: true },
  {
    key: 'code', labelKey: 'catalogs.field_code', labelFallback: 'Code', section: 'coding',
    kind: 'code', systemKey: 'coding_system',
  },
  {
    key: 'dose_schedule', labelKey: 'catalogs.field_dose_schedule', labelFallback: 'Dose schedule',
    section: 'schedule', kind: 'dose',
  },
  { key: 'description', labelKey: 'catalogs.field_description', labelFallback: 'Description', section: 'clinical', kind: 'richtext' },
  { key: 'contraindications', labelKey: 'catalogs.field_contraindications', labelFallback: 'Contraindications', section: 'clinical', kind: 'richtext' },
  {
    key: 'target_diseases', labelKey: 'catalogs.field_target_diseases', labelFallback: 'Target diseases',
    section: 'targets', kind: 'chips', variant: 'danger',
  },
  {
    key: 'side_effects', labelKey: 'catalogs.field_side_effects', labelFallback: 'Side effects',
    section: 'targets', kind: 'chips', variant: 'warning',
  },
];

const CONCEPT_STATUS_OPTIONS: Record<string, string> = {
  draft: 'Draft',
  active: 'Active',
  retired: 'Retired',
};

const CONCEPT_STATUS_TONES: Partial<Record<string, ChipVariant>> = {
  draft: 'warning',
  active: 'success',
  retired: 'neutral',
};

const CONCEPT_FIELDS: FieldDescriptor[] = [
  { key: 'name', labelKey: 'catalogs.field_name', labelFallback: 'Name', section: 'identity' },
  { key: 'slug', labelKey: 'catalogs.field_slug', labelFallback: 'Slug', section: 'identity', mono: true, copyable: true },
  { key: 'primary_kind', labelKey: 'catalogs.field_primary_kind', labelFallback: 'Primary kind', section: 'identity' },
  {
    key: 'kinds', labelKey: 'catalogs.field_kinds', labelFallback: 'Kinds', section: 'identity',
    kind: 'chips', variant: 'info',
  },
  { key: 'parent_slug', labelKey: 'catalogs.field_parent', labelFallback: 'Parent', section: 'identity' },
  {
    key: 'code', labelKey: 'catalogs.field_code', labelFallback: 'Code', section: 'coding',
    kind: 'code', systemKey: 'coding_system',
  },
  {
    key: 'status', labelKey: 'catalogs.field_status', labelFallback: 'Status', section: 'presentation',
    kind: 'enum', options: CONCEPT_STATUS_OPTIONS, tones: CONCEPT_STATUS_TONES,
  },
  {
    key: 'color', labelKey: 'catalogs.field_color', labelFallback: 'Color', section: 'presentation',
    kind: 'color',
  },
  {
    key: 'icon', labelKey: 'catalogs.field_icon', labelFallback: 'Icon', section: 'presentation',
    kind: 'icon', colorKey: 'color', hideWhenEmpty: false,
  },
  { key: 'display_order', labelKey: 'catalogs.field_display_order', labelFallback: 'Display order', section: 'presentation' },
  {
    key: 'aliases', labelKey: 'catalogs.field_aliases', labelFallback: 'Aliases', section: 'aliases',
    kind: 'chips', variant: 'neutral',
  },
  { key: 'description', labelKey: 'catalogs.field_description', labelFallback: 'Description', section: 'clinical', kind: 'richtext' },
  { key: 'meta_data', labelKey: 'catalogs.field_meta_data', labelFallback: 'Metadata', section: 'meta', hideWhenEmpty: false },
];

/** Per-type field lists. Unknown types → empty (everything → "additional"). */
export const TYPE_FIELDS: Record<CatalogType, FieldDescriptor[]> = {
  biomarker: BIOMARKER_FIELDS,
  medication: MEDICATION_FIELDS,
  allergy: ALLERGY_FIELDS,
  anatomy: ANATOMY_FIELDS,
  vaccine: VACCINE_FIELDS,
  concept: CONCEPT_FIELDS,
};

export type { FieldBase };
