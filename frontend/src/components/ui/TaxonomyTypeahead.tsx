import { useCallback, useEffect, useRef, useState } from 'react';
import { searchConcepts } from '../../services/conceptService';
import type { Concept, ConceptKind } from '../../types/concept';
import { Combobox, type ComboboxOption } from '@neuronection/assistant-ui';

interface TaxonomyTypeaheadProps {
  kind?: ConceptKind;
  /** Currently-selected concept id (drives the check mark in results). */
  value?: string | null;
  /** Pre-populate the selected display (used by edit forms to show an
   *  existing link without requiring the user to re-search). */
  initialConcept?: Concept | null;
  onSelect: (concept: Concept | null) => void;
  placeholder?: string;
  className?: string;
  clearable?: boolean;
}

/**
 * Server-backed concept picker over the library async combobox (debounced
 * `searchConcepts` stays app-side).
 */
export default function TaxonomyTypeahead({
  kind,
  value,
  initialConcept,
  onSelect,
  placeholder = 'Search…',
  className = '',
  clearable = true,
}: TaxonomyTypeaheadProps) {
  const [term, setTerm] = useState('');
  const [results, setResults] = useState<Concept[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Concept | null>(initialConcept ?? null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const doSearch = useCallback(
    async (t: string) => {
      if (t.trim().length < 1) {
        setResults([]);
        return;
      }
      setLoading(true);
      try {
        const data = await searchConcepts(t, kind, 20);
        setResults(data);
      } catch {
        setResults([]);
      } finally {
        setLoading(false);
      }
    },
    [kind],
  );

  useEffect(() => {
    if (!term) return;
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => doSearch(term), 300);
    return () => clearTimeout(debounceRef.current);
  }, [term, doSearch]);

  const options: ComboboxOption[] = results.map((c) => ({
    value: c.id,
    label: c.name,
    description: c.description ?? undefined,
  }));
  const selectedOption = selected
    ? { value: selected.id, label: selected.name, description: selected.description ?? undefined }
    : null;
  if (selectedOption && !options.some((o) => o.value === selectedOption.value)) {
    options.unshift(selectedOption);
  }

  return (
    <Combobox
      className={className}
      options={options}
      value={selectedOption?.value ?? ''}
      onChange={(id) => {
        if (!id) {
          setSelected(null);
          onSelect(null);
          return;
        }
        const c = results.find((r) => r.id === id) ?? (selected?.id === id ? selected : undefined);
        if (c) {
          setSelected(c);
          onSelect(c);
        }
      }}
      onSearchChange={setTerm}
      loading={loading}
      clearable={clearable}
      placeholder={placeholder}
      searchPlaceholder={placeholder}
      searchLabel="Search concepts"
      emptyLabel={term ? 'No matches' : 'Start typing to search…'}
    />
  );
}
