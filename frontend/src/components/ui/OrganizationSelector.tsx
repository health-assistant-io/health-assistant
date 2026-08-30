import { useState } from 'react';
import { Plus } from 'lucide-react';
import type { Organization } from '../../types/clinical';
import { Combobox, type ComboboxOption } from '@neuronection/assistant-ui';

interface Props {
  organizations: Organization[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onCreate?: (name: string) => Promise<void>;
  placeholder?: string;
  className?: string;
}

/** Single-organization picker over the library `Combobox` (create stays app-side). */
export const OrganizationSelector: React.FC<Props> = ({
  organizations,
  selectedId,
  onSelect,
  onCreate,
  placeholder = 'Select organization…',
  className = '',
}) => {
  const [term, setTerm] = useState('');
  const [creating, setCreating] = useState(false);

  const options: ComboboxOption[] = organizations.map((o) => ({
    value: o.id,
    label: o.name,
    description: Array.isArray(o.type) ? undefined : o.type,
  }));

  const exactMatch = organizations.some(
    (o) => o.name.toLowerCase() === term.trim().toLowerCase(),
  );
  const canCreate = Boolean(onCreate) && term.trim().length > 1 && !exactMatch && !creating;

  const handleCreate = async () => {
    if (!canCreate || !onCreate) return;
    setCreating(true);
    try {
      await onCreate(term.trim());
      setTerm('');
    } catch (err) {
      console.error('Failed to create organization', err);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <Combobox
        options={options}
        value={selectedId ?? ''}
        onChange={(id) => onSelect(id || null)}
        onSearchChange={setTerm}
        clearable={Boolean(selectedId)}
        placeholder={placeholder}
        searchPlaceholder={placeholder}
        emptyLabel="No organizations match"
      />
      {canCreate && (
        <button
          type="button"
          onClick={() => void handleCreate()}
          className="self-start text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline disabled:opacity-50"
        >
          <Plus className="mr-1 inline size-3" aria-hidden />
          Create “{term.trim()}”
        </button>
      )}
    </div>
  );
};

export default OrganizationSelector;
