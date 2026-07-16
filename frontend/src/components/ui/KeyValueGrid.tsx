/**
 * KeyValueGrid — the read-only label/value grid extracted from the original
 * `CatalogItemInfo` field list, promoted to a reusable primitive.
 *
 * Renders a semantic `<dl>` with a 3-column grid (label / value); values may
 * be marked `mono` (monospace, for codes/uuids) and `copyable` (appends a
 * {@link CopyButton}). Empty values render a muted dash. This is the generic
 * fallback renderer for catalog fields without a specialized component, and
 * the "Additional fields" catch-all section.
 *
 * Extracted verbatim from `CatalogItemInfo.tsx`'s `renderEntry` so the
 * default look is unchanged — only its location and reusability improve.
 */
import React from 'react';
import { CopyButton } from './CopyButton';

export interface KeyValueEntry {
  key: string;
  label: React.ReactNode;
  value: React.ReactNode;
  /** Render the value in a monospace font (codes, ids). */
  mono?: boolean;
  /** Append a copy button copying `copyValue ?? stringified value`. */
  copyable?: boolean;
  copyValue?: string;
}

interface KeyValueGridProps {
  entries: ReadonlyArray<KeyValueEntry>;
  className?: string;
}

/** Stringify a React value for clipboard copying. */
function toCopyText(value: React.ReactNode): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  if (Array.isArray(value)) return value.map(toCopyText).join(', ');
  if (typeof value === 'object' && 'props' in (value as object)) {
    // Best-effort text extraction for an element's children.
    const { children } = (value as React.ReactElement).props ?? {};
    return toCopyText(children);
  }
  return String(value);
}

function renderValueNode(entry: KeyValueEntry): React.ReactNode {
  const { value, mono, copyable, copyValue } = entry;
  if (value === null || value === undefined || value === '') {
    return <span className="text-gray-400">—</span>;
  }
  const valueCls = mono ? 'font-mono break-all' : 'break-words';
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={valueCls}>{value}</span>
      {copyable && (
        <CopyButton value={copyValue ?? toCopyText(value)} size={12} />
      )}
    </span>
  );
}

export const KeyValueGrid: React.FC<KeyValueGridProps> = ({
  entries,
  className = '',
}) => {
  if (entries.length === 0) return null;
  return (
    <dl className={`divide-y divide-gray-100 dark:divide-gray-700 ${className}`}>
      {entries.map((entry) => (
        <div
          key={entry.key}
          className="py-1.5 grid grid-cols-3 gap-3 text-sm"
        >
          <dt className="text-gray-500 dark:text-gray-400 col-span-1">
            {entry.label}
          </dt>
          <dd className="text-gray-800 dark:text-gray-100 col-span-2">
            {renderValueNode(entry)}
          </dd>
        </div>
      ))}
    </dl>
  );
};

export default KeyValueGrid;
