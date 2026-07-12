/**
 * ChipInput — a modern tag/chip input for free-text lists (aliases, keywords,
 * synonyms). Type a value, press Enter or comma to commit it as a removable
 * pill; Backspace on an empty input removes the last chip; pasting
 * "FBS, Glucose" creates two chips at once.
 *
 * Replaces the comma-separated `<input>` + `.split(',')` pattern used across
 * the catalog forms. Controlled: `value: string[]` + `onChange`.
 */
import React, { useRef, useState } from 'react';
import { X } from 'lucide-react';

interface ChipInputProps {
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
  /** Single-character commit triggers (default: comma). Enter is always a
   *  trigger. These also split pasted content into multiple chips. */
  separators?: string[];
  className?: string;
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export const ChipInput: React.FC<ChipInputProps> = ({
  value,
  onChange,
  placeholder,
  separators = [','],
  className = '',
}) => {
  const [draft, setDraft] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const commit = (raw: string) => {
    const sepPattern = new RegExp(
      `[${separators.map(escapeRegex).join('')}\\n\\r]`,
    );
    const existing = new Set(value.map((v) => v.toLowerCase()));
    const next = [...value];
    for (const part of raw.split(sepPattern)) {
      const trimmed = part.trim();
      if (trimmed && !existing.has(trimmed.toLowerCase())) {
        next.push(trimmed);
        existing.add(trimmed.toLowerCase());
      }
    }
    if (next.length !== value.length) onChange(next);
    setDraft('');
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      commit(draft);
    } else if (separators.includes(e.key)) {
      // Comma (or any configured separator) commits the draft instead of
      // inserting a literal character.
      e.preventDefault();
      commit(draft);
    } else if (e.key === 'Backspace' && draft === '' && value.length > 0) {
      e.preventDefault();
      onChange(value.slice(0, -1));
    }
  };

  const handlePaste = (e: React.ClipboardEvent<HTMLInputElement>) => {
    e.preventDefault();
    const text = e.clipboardData.getData('text');
    commit(`${draft}${text}`);
  };

  const removeAt = (index: number) => {
    onChange(value.filter((_, i) => i !== index));
  };

  return (
    <div
      onClick={() => inputRef.current?.focus()}
      className={`flex flex-wrap items-center gap-1.5 min-h-[42px] w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-sm focus-within:ring-2 focus-within:ring-blue-500 outline-none cursor-text ${className}`}
    >
      {value.map((chip, i) => (
        <span
          key={`${chip}-${i}`}
          className="inline-flex items-center gap-1 pl-2.5 pr-1.5 py-0.5 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300 rounded-md text-xs font-medium"
        >
          {chip}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              removeAt(i);
            }}
            className="text-blue-400 hover:text-red-500 dark:text-blue-400 dark:hover:text-red-400 transition-colors"
            aria-label={`Remove ${chip}`}
          >
            <X className="w-3 h-3" />
          </button>
        </span>
      ))}
      <input
        ref={inputRef}
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
        onBlur={() => {
          if (draft.trim()) commit(draft);
        }}
        placeholder={value.length === 0 ? placeholder : ''}
        className="flex-1 min-w-[100px] bg-transparent outline-none text-sm text-gray-800 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500"
      />
    </div>
  );
};

export default ChipInput;
