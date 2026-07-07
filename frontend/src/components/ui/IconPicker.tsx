import { useState, useEffect, useRef, useMemo } from 'react';
import * as LucideIcons from 'lucide-react';
import { Search, ChevronDown } from 'lucide-react';
import { DynamicIcon, type IconConfig } from './DynamicIcon';

interface IconPickerProps {
  value: IconConfig;
  onChange: (icon: IconConfig) => void;
  /** Tint the preview swatch background (uses 20% alpha). */
  color?: string | null;
  className?: string;
}

/**
 * Searchable Lucide icon picker rendered as a dropdown popover.
 *
 * The button shows the currently-selected icon in a colored swatch + its
 * name; clicking opens a panel with a debounced-free search input over the
 * full Lucide icon set and a scrollable grid of matches. Selected state is
 * owned by the parent (controlled via ``value`` / ``onChange``).
 */
export function IconPicker({ value, onChange, color = '#3b82f6', className = '' }: IconPickerProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [isOpen]);

  const icons = useMemo(() => {
    const q = search.trim().toLowerCase();
    return Object.keys(LucideIcons)
      .filter((k) => /^[A-Z]/.test(k) && typeof (LucideIcons as any)[k] === 'function')
      .filter((k) => !q || k.toLowerCase().includes(q))
      .slice(0, 60);
  }, [search]);

  return (
    <div className={`relative ${className}`} ref={containerRef}>
      <button
        type="button"
        onClick={() => setIsOpen((v) => !v)}
        className="w-full flex items-center gap-2 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-2 text-sm hover:border-blue-400 dark:hover:border-blue-500 transition-colors"
      >
        <div
          className="flex items-center justify-center w-7 h-7 rounded-lg shrink-0"
          style={{ backgroundColor: `${color || '#3b82f6'}20`, color: color || '#3b82f6' }}
        >
          <DynamicIcon icon={value} className="w-4 h-4" />
        </div>
        <span className="flex-1 text-left truncate text-slate-700 dark:text-slate-200">
          {value.value}
        </span>
        <ChevronDown className="w-4 h-4 text-slate-400" />
      </button>

      {isOpen && (
        <div className="absolute z-50 mt-1 w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 shadow-lg">
          <div className="flex items-center gap-2 p-2 border-b border-slate-100 dark:border-slate-700">
            <Search className="w-3.5 h-3.5 text-slate-400 shrink-0" />
            <input
              autoFocus
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search icons..."
              className="flex-1 bg-transparent outline-none text-sm text-slate-900 dark:text-slate-100 placeholder-slate-400"
              onKeyDown={(e) => { if (e.key === 'Escape') setIsOpen(false); }}
            />
          </div>
          <div className="max-h-44 overflow-y-auto grid grid-cols-7 gap-1 p-2">
            {icons.map((name) => {
              const Icon = (LucideIcons as any)[name] as React.ComponentType<{ className?: string }>;
              const isSelected = value.value === name;
              return (
                <button
                  key={name}
                  type="button"
                  onClick={() => { onChange({ type: 'lucide', value: name }); setIsOpen(false); setSearch(''); }}
                  title={name}
                  className={`flex items-center justify-center w-8 h-8 rounded transition-colors ${
                    isSelected
                      ? 'bg-blue-100 dark:bg-blue-900 text-blue-600'
                      : 'hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-600 dark:text-slate-300'
                  }`}
                >
                  <Icon className="w-4 h-4" />
                </button>
              );
            })}
            {icons.length === 0 && (
              <div className="col-span-7 px-2 py-4 text-center text-xs text-slate-400">
                No icons match "{search}"
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default IconPicker;
