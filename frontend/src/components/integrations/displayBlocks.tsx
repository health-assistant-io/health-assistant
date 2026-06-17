import React from 'react';
import { Copy } from 'lucide-react';
import { toast } from 'react-toastify';
import type {
  DisplayBlock,
  KvBlock,
  ListBlock,
  TableBlock,
  JsonBlock,
  TextBlock,
  CodeBlock,
} from '../../services/integrationService';

// ---- per-type renderers -------------------------------------------------------

const KvRenderer: React.FC<{ block: KvBlock }> = ({ block }) => (
  <div className="rounded-xl border border-gray-100 dark:border-dark-border overflow-hidden">
    <dl className="divide-y divide-gray-100 dark:divide-dark-border">
      {Object.entries(block.items || {}).map(([k, v], idx) => (
        <div
          key={idx}
          className="flex items-center justify-between px-4 py-2.5 bg-gray-50/50 dark:bg-dark-bg/30"
        >
          <dt className="text-xs font-bold text-gray-500 dark:text-dark-muted uppercase tracking-wide mr-4 shrink-0">
            {k}
          </dt>
          <dd className="text-sm text-gray-900 dark:text-dark-text font-mono break-all text-right">
            {renderValue(v)}
          </dd>
        </div>
      ))}
    </dl>
  </div>
);

const renderValue = (v: any): React.ReactNode => {
  if (v === null || v === undefined) return <span className="text-gray-400 italic">—</span>;
  if (v === true) return <span className="text-emerald-600 font-bold">true</span>;
  if (v === false) return <span className="text-red-500 font-bold">false</span>;
  if (typeof v === 'number') return String(v);
  if (typeof v === 'string') {
    // Status heuristic: colorize common status words.
    const lower = v.toLowerCase();
    if (['connected', 'active', 'ok', 'success', 'ready'].includes(lower))
      return <span className="text-emerald-600 font-bold">{v}</span>;
    if (['error', 'failed', 'disconnected', 'offline', 'expired'].includes(lower))
      return <span className="text-red-500 font-bold">{v}</span>;
    return v;
  }
  return JSON.stringify(v);
};

const ListRenderer: React.FC<{ block: ListBlock }> = ({ block }) => {
  const items = block.items || [];
  if (items.length === 0) {
    return <p className="text-sm text-gray-400 italic py-2">(empty)</p>;
  }
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item, idx) => (
        <span
          key={idx}
          className="inline-flex items-center px-3 py-1 rounded-lg bg-gray-100 dark:bg-dark-bg text-sm font-mono text-gray-800 dark:text-dark-text border border-gray-200 dark:border-dark-border"
        >
          {item}
        </span>
      ))}
    </div>
  );
};

const TableRenderer: React.FC<{ block: TableBlock }> = ({ block }) => {
  const { columns = [], rows = [] } = block;
  if (rows.length === 0) {
    return <p className="text-sm text-gray-400 italic py-2">(no rows)</p>;
  }
  return (
    <div className="max-h-80 overflow-auto rounded-xl border border-gray-100 dark:border-dark-border custom-scrollbar">
      <table className="min-w-full divide-y divide-gray-100 dark:divide-dark-border">
        <thead className="bg-gray-50 dark:bg-dark-bg sticky top-0 z-10">
          <tr>
            {columns.map((col, i) => (
              <th
                key={i}
                className="px-4 py-2 text-left text-xs font-bold text-gray-500 dark:text-dark-muted uppercase tracking-wider"
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-50 dark:divide-dark-border bg-white dark:bg-dark-surface">
          {rows.map((row, ri) => (
            <tr key={ri} className="hover:bg-gray-50 dark:hover:bg-dark-bg/50">
              {row.map((cell, ci) => (
                <td
                  key={ci}
                  className={`px-4 py-2 text-sm text-gray-900 dark:text-dark-text ${
                    ci === 0
                      ? 'whitespace-nowrap font-mono text-xs'
                      : 'whitespace-normal break-words'
                  } align-top`}
                >
                  {cell === null || cell === undefined ? (
                    <span className="text-gray-400 italic">—</span>
                  ) : (
                    String(cell)
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const JsonRenderer: React.FC<{ block: JsonBlock }> = ({ block }) => {
  const text = JSON.stringify(block.data, null, 2);
  return (
    <pre className="bg-gray-50 dark:bg-dark-bg p-4 rounded-xl text-xs text-gray-700 dark:text-dark-muted overflow-x-auto border border-gray-100 dark:border-dark-border font-mono max-h-96 custom-scrollbar">
      {text}
    </pre>
  );
};

const TextRenderer: React.FC<{ block: TextBlock }> = ({ block }) => (
  <p className="text-sm text-gray-700 dark:text-dark-text whitespace-pre-wrap leading-relaxed">
    {block.content}
  </p>
);

const CodeRenderer: React.FC<{ block: CodeBlock }> = ({ block }) => {
  const handleCopy = () => {
    navigator.clipboard.writeText(block.content).then(
      () => toast.success('Copied to clipboard'),
      () => toast.error('Failed to copy')
    );
  };
  return (
    <div className="relative group">
      <pre className="bg-gray-900 dark:bg-black/40 p-4 rounded-xl text-xs text-gray-100 dark:text-gray-200 overflow-x-auto border border-gray-800 dark:border-dark-border font-mono">
        {block.content}
      </pre>
      <button
        onClick={handleCopy}
        className="absolute top-2 right-2 p-1.5 rounded-lg bg-gray-800/80 text-gray-300 hover:bg-gray-700 hover:text-white opacity-0 group-hover:opacity-100 transition-opacity"
        title="Copy"
      >
        <Copy className="w-3.5 h-3.5" />
      </button>
    </div>
  );
};

// ---- dispatcher ---------------------------------------------------------------

const BLOCK_RENDERERS: Record<string, React.FC<{ block: any }>> = {
  kv: KvRenderer as any,
  list: ListRenderer as any,
  table: TableRenderer as any,
  json: JsonRenderer as any,
  text: TextRenderer as any,
  code: CodeRenderer as any,
};

export const DisplayBlockRenderer: React.FC<{ block: DisplayBlock }> = ({ block }) => {
  const Renderer = BLOCK_RENDERERS[block.type];
  if (Renderer) {
    return <Renderer block={block} />;
  }
  // Unknown block type -> JSON fallback so the data is still visible.
  return <JsonRenderer block={{ type: 'json', title: block.title, data: block }} />;
};

export default DisplayBlockRenderer;
