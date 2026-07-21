/**
 * CodingSystemSelect — reusable dropdown for picking a clinical coding system.
 *
 * Backed by the single source of truth in `config/codingSystems.ts`. Replaces
 * the per-form hardcoded `<option>` lists + local `CODING_SYSTEMS` constants
 * (BiomarkerForm, VaccineForm, ConceptForm, AnatomyForm, ClinicalEventForm).
 *
 * Usage:
 *   <CodingSystemSelect domain="vaccine" value={v} onChange={setV} />
 *   <CodingSystemSelect systems={customList} includeEmpty value={v} onChange={setV} />
 *
 * The component is agnostic to the field name — callers bind `value`/`onChange`
 * to whichever column they use (`coding_system`, `standard_system`, …). Pass
 * `includeEmpty` for nullable fields (renders a leading "—" option whose
 * selection calls `onChange('')`).
 */
import React from 'react';
import {
  CODING_SYSTEMS,
  getCodingSystemsForDomain,
  type CodingDomain,
  type CodingSystemDef,
} from '../../config/codingSystems';

export interface CodingSystemSelectProps {
  value: string | null | undefined;
  onChange: (value: string) => void;
  /** Restrict the list to the systems relevant for this entity. */
  domain?: CodingDomain;
  /** Explicit system list — overrides `domain` when provided. */
  systems?: CodingSystemDef[];
  /** Render a leading empty option (value `''`) for nullable fields. */
  includeEmpty?: boolean;
  /** Label for the empty option (default '—'). */
  emptyLabel?: string;
  /** Full class for the <select>. Defaults to the compact catalog-form style;
   *  pass your own to match a different form's look (the option list — the
   *  shared part — stays sourced from the registry regardless). */
  className?: string;
  id?: string;
  disabled?: boolean;
}

const BASE_CLASS =
  'w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none';

export const CodingSystemSelect: React.FC<CodingSystemSelectProps> = ({
  value,
  onChange,
  domain,
  systems,
  includeEmpty = false,
  emptyLabel = '—',
  className = '',
  id,
  disabled = false,
}) => {
  const list = systems ?? (domain ? getCodingSystemsForDomain(domain) : CODING_SYSTEMS);
  return (
    <select
      id={id}
      value={value ?? ''}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      className={className || BASE_CLASS}
    >
      {includeEmpty && <option value="">{emptyLabel}</option>}
      {list.map((s) => (
        <option key={s.value} value={s.value} title={s.hint}>
          {s.label}
        </option>
      ))}
    </select>
  );
};

export default CodingSystemSelect;
