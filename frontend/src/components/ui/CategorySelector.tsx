import { useState } from 'react';
import { Plus } from 'lucide-react';
interface Category {
  id: string;
  name: string;
  slug?: string;
  icon?: string;
  color?: string;
}
import { Combobox, type ComboboxOption } from '@neuronection/assistant-ui';

interface Props {
  categories: Category[];
  selectedName: string;
  onSelect: (name: string) => void;
  onCreate?: (name: string) => Promise<void>;
  placeholder?: string;
  className?: string;
}

/** Category picker over the library `Combobox` (create stays app-side). */
export const CategorySelector: React.FC<Props> = ({
  categories,
  selectedName,
  onSelect,
  onCreate,
  placeholder = 'Select category…',
  className = '',
}) => {
  const [term, setTerm] = useState('');
  const [creating, setCreating] = useState(false);

  const options: ComboboxOption[] = categories.map((c) => ({
    value: c.name,
    label: c.name,
  }));

  const exactMatch = categories.some(
    (c) => c.name.toLowerCase() === term.trim().toLowerCase(),
  );
  const canCreate = Boolean(onCreate) && term.trim().length > 1 && !exactMatch && !creating;

  const handleCreate = async () => {
    if (!canCreate || !onCreate) return;
    setCreating(true);
    try {
      await onCreate(term.trim());
      setTerm('');
    } catch (err) {
      console.error('Failed to create category', err);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <Combobox
        options={options}
        value={selectedName}
        onChange={(name) => onSelect(name)}
        onSearchChange={setTerm}
        clearable={Boolean(selectedName)}
        placeholder={placeholder}
        searchPlaceholder={placeholder}
        emptyLabel="No categories match"
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

export default CategorySelector;
