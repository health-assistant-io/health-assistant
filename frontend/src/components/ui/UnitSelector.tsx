import { useState } from 'react';
import { Plus } from 'lucide-react';
import biomarkerService from '../../services/biomarkerService';
import type { Unit } from '../../types/biomarker';
import { formatUnit } from '../../utils/biomarkerUtils';
import { Combobox, type ComboboxOption } from '@neuronection/assistant-ui';

interface Props {
  units: Unit[];
  selectedId?: string;
  selectedSymbol?: string;
  onSelect: (unit: Unit) => void;
  onUnitsUpdated: (newUnits: Unit[]) => void;
  placeholder?: string;
  className?: string;
}

/** Unit picker over the library `Combobox`; inline unit creation stays app-side. */
export const UnitSelector: React.FC<Props> = ({
  units,
  selectedId,
  selectedSymbol,
  onSelect,
  onUnitsUpdated,
  placeholder = 'Select unit…',
  className = '',
}) => {
  const [term, setTerm] = useState('');
  const [creating, setCreating] = useState(false);

  const selectedUnit = selectedId
    ? units.find((u) => u.id === selectedId)
    : units.find((u) => u.symbol === selectedSymbol);

  const options: ComboboxOption[] = units.map((u) => ({
    value: u.id,
    label: formatUnit(u.symbol),
    description: u.name !== u.symbol ? u.name : undefined,
  }));

  const exactMatch = units.some(
    (u) => u.symbol.toLowerCase() === term.trim().toLowerCase(),
  );
  const canCreate = term.trim().length > 0 && !exactMatch && !creating;

  const handleCreate = async () => {
    if (!canCreate) return;
    setCreating(true);
    try {
      const newUnit = await biomarkerService.createUnit({
        symbol: term,
        name: term,
        quantity_type: 'other',
      });
      onUnitsUpdated([...units, newUnit]);
      onSelect(newUnit);
      setTerm('');
    } catch (err) {
      console.error('Failed to create unit', err);
      alert('Failed to create unit. It might already exist.');
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <Combobox
        options={options}
        value={selectedUnit?.id ?? ''}
        onChange={(id) => {
          const unit = units.find((u) => u.id === id);
          if (unit) onSelect(unit);
        }}
        onSearchChange={setTerm}
        clearable={Boolean(selectedUnit)}
        placeholder={placeholder}
        searchPlaceholder={placeholder}
        emptyLabel="No units match"
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

export default UnitSelector;
