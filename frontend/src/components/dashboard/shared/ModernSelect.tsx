import React from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, Check } from 'lucide-react';

export interface ModernSelectProps {
  value: string;
  options: any[];
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  discreet?: boolean;
}

export const ModernSelect: React.FC<ModernSelectProps> = ({ value, options, onChange, placeholder, className, discreet }) => {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = React.useState(false);
  const dropdownRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const getDisplayValue = () => {
    if (!value) return placeholder;
    const option = options.find(opt => (opt.slug === value || opt.name === value || opt === value));
    return typeof option === 'object' ? option.name : (option || value);
  };

  return (
    <div className={`relative ${className} nodrag`} ref={dropdownRef}>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); setIsOpen(!isOpen); }}
        className={`w-full flex items-center justify-between transition-all outline-none focus:ring-2 focus:ring-blue-500/20 ${discreet 
          ? 'px-2 py-1 bg-transparent border-none text-[10px] font-black uppercase tracking-widest text-gray-400 hover:text-blue-500 hover:bg-gray-100 dark:hover:bg-dark-surface rounded-lg' 
          : 'px-3 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-[10px] font-black uppercase tracking-widest text-gray-500 dark:text-dark-muted hover:bg-gray-100 dark:hover:bg-dark-surface hover:border-blue-300 shadow-sm'}`}
      >
        <span className="truncate mr-2">{getDisplayValue()}</span>
        <ChevronDown className={`w-3 h-3 transition-transform duration-300 ${isOpen ? 'rotate-180' : ''}`} />
      </button>
      
      {isOpen && (
        <div className="absolute top-full right-0 mt-1.5 z-[150] bg-white/95 dark:bg-dark-surface/95 backdrop-blur-md border border-gray-200 dark:border-dark-border rounded-xl shadow-2xl overflow-hidden max-h-60 min-w-[180px] overflow-y-auto custom-scrollbar animate-in fade-in slide-in-from-top-1 duration-200">
          <div className="py-1.5">
            {options.length === 0 ? (
              <div className="px-4 py-3 text-[10px] text-gray-400 italic">No options available</div>
            ) : (
              options.map((opt) => {
                const label = typeof opt === 'object' ? opt.name : opt;
                const val = typeof opt === 'object' ? (opt.slug || opt.id) : opt;
                const isSelected = value === val || value === label;

                return (
                  <button
                    key={val}
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onChange(val);
                      setIsOpen(false);
                    }}
                    className={`w-full text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider transition-all flex items-center justify-between ${isSelected ? 'bg-blue-600 text-white' : 'text-gray-600 dark:text-dark-text hover:bg-blue-50 dark:hover:bg-blue-900/40'}`}
                  >
                    <span>{label}</span>
                    {isSelected && <Check className="w-3 h-3 ml-2" />}
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
};
