import { useState } from 'react';
import { Plus } from 'lucide-react';
import type { Doctor } from '../../services/doctorService';
import {
  Combobox,
  ComboboxMulti,
  type ComboboxOption,
} from '@neuronection/assistant-ui';

interface Props {
  doctors: Doctor[];
  selectedIds: string[];
  onSelect: (id: string) => void;
  onDeselect: (id: string) => void;
  onCreateDoctor: (name: string) => Promise<void>;
  placeholder?: string;
  className?: string;
}

const doctorName = (d: Doctor) =>
  d.name.toLowerCase().startsWith('dr') ? d.name : `Dr. ${d.name}`;

/** Multi-doctor picker over the library `ComboboxMulti` (create stays app-side). */
export const DoctorSelector: React.FC<Props> = ({
  doctors,
  selectedIds,
  onSelect,
  onDeselect,
  onCreateDoctor,
  placeholder = 'Select Doctors…',
  className = '',
}) => {
  const [term, setTerm] = useState('');
  const [creating, setCreating] = useState(false);

  const options: ComboboxOption[] = doctors.map((d) => ({
    value: d.id,
    label: doctorName(d),
    description: d.specialty,
  }));

  const exactMatch = doctors.some(
    (d) => d.name.toLowerCase() === term.trim().toLowerCase(),
  );
  const canCreate = term.trim().length > 1 && !exactMatch && !creating;

  const handleCreate = async () => {
    if (!canCreate) return;
    setCreating(true);
    try {
      const normalizedName = term.replace(/^(dr\.?\s*)+/i, '').trim();
      await onCreateDoctor(normalizedName);
      setTerm('');
    } catch (err) {
      console.error('Failed to create doctor', err);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <ComboboxMulti
        options={options}
        value={selectedIds}
        onChange={(next) => {
          const added = next.find((id) => !selectedIds.includes(id));
          const removed = selectedIds.find((id) => !next.includes(id));
          if (added) onSelect(added);
          if (removed) onDeselect(removed);
        }}
        onSearchChange={setTerm}
        placeholder={placeholder}
        searchPlaceholder={placeholder}
        emptyLabel="No doctors match"
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

export default DoctorSelector;
