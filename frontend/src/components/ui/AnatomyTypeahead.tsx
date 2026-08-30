import { useCallback, useEffect, useRef, useState } from 'react';
import { anatomyService } from '../../services/anatomyService';
import type { AnatomyStructure } from '../../types/anatomy';
import { Combobox, type ComboboxOption } from '@neuronection/assistant-ui';

export interface AnatomyTypeaheadSelection {
  id: string;
  name: string;
  slug: string;
}

interface AnatomyTypeaheadProps {
  value?: string | null;
  initial?: AnatomyStructure | null;
  onSelect: (selection: AnatomyTypeaheadSelection | null) => void;
  placeholder?: string;
  className?: string;
  clearable?: boolean;
}

/**
 * Server-backed searchable picker for ``anatomy_structures`` over the library
 * async combobox (debounced fetch stays app-side).
 */
export default function AnatomyTypeahead({
  value,
  initial,
  onSelect,
  placeholder = 'Search anatomy…',
  className = '',
  clearable = true,
}: AnatomyTypeaheadProps) {
  const [term, setTerm] = useState('');
  const [results, setResults] = useState<AnatomyStructure[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedOption, setSelectedOption] = useState<ComboboxOption | null>(
    initial ? { value: initial.id, label: initial.name, description: initial.slug } : null,
  );
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const doSearch = useCallback(async (t: string) => {
    if (!t.trim()) {
      setResults([]);
      return;
    }
    setLoading(true);
    try {
      const res = await anatomyService.list({ search: t, limit: 20 });
      setResults(res.items);
    } catch {
      setResults([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!term) return;
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => doSearch(term), 300);
    return () => clearTimeout(debounceRef.current);
  }, [term, doSearch]);

  const options: ComboboxOption[] = results.map((s) => ({
    value: s.id,
    label: s.name,
    description: s.description ? `${s.slug} · ${s.description}` : s.slug,
  }));
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
          setSelectedOption(null);
          onSelect(null);
          return;
        }
        const s = results.find((r) => r.id === id) ?? (selectedOption?.value === id ? initial : undefined);
        if (s) {
          setSelectedOption({ value: s.id, label: s.name, description: s.slug });
          onSelect({ id: s.id, name: s.name, slug: s.slug });
        }
      }}
      onSearchChange={setTerm}
      loading={loading}
      clearable={clearable}
      placeholder={placeholder}
      searchPlaceholder={placeholder}
      searchLabel="Search anatomy"
      emptyLabel={term ? 'No anatomy matches' : 'Start typing to search anatomy…'}
    />
  );
}
