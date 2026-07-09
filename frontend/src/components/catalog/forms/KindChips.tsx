/**
 * Reusable multi-select chip toggle for ``ConceptKind`` values.
 *
 * Used by the concept create/edit form to pick which domain(s) a concept
 * belongs to (e.g. a "Blood Laboratory" concept is both
 * ``examination_category`` and ``biomarker_class``). At least one kind is
 * required (enforced server-side; this component warns client-side).
 *
 * Generic enough to reuse for any future multi-enum-tag entity.
 */
import React from 'react';
import {
  CONCEPT_KIND_LABELS,
  KIND_COLORS,
  type ConceptKind,
} from '../../../types/concept';

interface KindChipsProps {
  value: ConceptKind[];
  onChange: (kinds: ConceptKind[]) => void;
}

export const KindChips: React.FC<KindChipsProps> = ({ value, onChange }) => {
  const selected = new Set(value);
  const allKinds = Object.keys(CONCEPT_KIND_LABELS) as ConceptKind[];

  const toggle = (kind: ConceptKind) => {
    const next = new Set(selected);
    if (next.has(kind)) next.delete(kind);
    else next.add(kind);
    onChange([...next]);
  };

  return (
    <div className="flex flex-wrap gap-1.5">
      {allKinds.map((kind) => {
        const active = selected.has(kind);
        const color = KIND_COLORS[kind];
        return (
          <button
            key={kind}
            type="button"
            onClick={() => toggle(kind)}
            className={`px-2 py-0.5 text-[11px] font-bold rounded-full border transition-all ${
              active
                ? 'text-white border-transparent'
                : 'border-gray-200 dark:border-gray-600 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'
            }`}
            style={active ? { backgroundColor: color } : undefined}
          >
            {CONCEPT_KIND_LABELS[kind]}
          </button>
        );
      })}
      {value.length === 0 && (
        <p className="w-full text-[11px] text-amber-500">
          At least one kind is required.
        </p>
      )}
    </div>
  );
};
