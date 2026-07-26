import React, { useEffect, useMemo, useRef, useState } from 'react';
import { X, Search, FileText, Plus, Server, Cloud, Globe, LayoutGrid } from 'lucide-react';
import type { IntegrationManifest } from '../../services/integrationService';
import { Portal } from '../ui/Portal';

interface Props {
  open: boolean;
  available: IntegrationManifest[];
  /** Domains the user has already connected (to badge cards). */
  connectedDomains?: Set<string>;
  onClose: () => void;
  onAdd: (domain: string) => void;
  onDocs: (domain: string) => void;
}

const UNCATEGORIZED = 'Uncategorized';

const getCategories = (items: IntegrationManifest[]): string[] =>
  Array.from(new Set(items.flatMap((i) => i.categories?.length ? i.categories : [UNCATEGORIZED]))).sort();

const getAccessIcon = (type?: string) => {
  switch (type) {
    case 'local':
      return <Server className="h-4 w-4 text-gray-500" aria-label="Local" />;
    case 'cloud':
      return <Cloud className="h-4 w-4 text-blue-500" aria-label="Cloud" />;
    case 'hybrid':
      return <Globe className="h-4 w-4 text-purple-500" aria-label="Hybrid" />;
    default:
      return null;
  }
};

/**
 * Modern "Browse & Connect" catalog modal for the integrations page.
 *
 * Replaces the old horizontal-scroll category strip + always-visible grid
 * with a focused, searchable popup: a category sidebar (vertical, no
 * horizontal scroll) + a search box + a scrollable card grid. Keeps the
 * page's active-connections list untouched.
 */
export const BrowseIntegrationsModal: React.FC<Props> = ({
  open,
  available,
  connectedDomains,
  onClose,
  onAdd,
  onDocs,
}) => {
  const [query, setQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Reset filters each time the modal opens; autofocus the search box.
  useEffect(() => {
    if (open) {
      setQuery('');
      setSelectedCategory(null);
      const t = window.setTimeout(() => inputRef.current?.focus(), 60);
      return () => window.clearTimeout(t);
    }
  }, [open]);

  // Esc closes the modal.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const categories = useMemo(() => getCategories(available), [available]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return available.filter((integration) => {
      const itemCategories = integration.categories?.length ? integration.categories : [UNCATEGORIZED];
      const inCategory = !selectedCategory || itemCategories.includes(selectedCategory);
      if (!inCategory) return false;
      if (!q) return true;
      return (
        integration.name.toLowerCase().includes(q) ||
        integration.domain.toLowerCase().includes(q) ||
        (integration.description ?? '').toLowerCase().includes(q) ||
        (integration.categories ?? []).some((c) => c.toLowerCase().includes(q))
      );
    });
  }, [available, query, selectedCategory]);

  if (!open) return null;

  return (
    <Portal>
      <div
        className="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-in fade-in duration-200"
        onClick={onClose}
      >
        <div
          className="bg-white dark:bg-dark-surface w-full max-w-5xl rounded-2xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200 flex flex-col max-h-[90vh] z-[10000]"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="p-5 border-b border-gray-100 dark:border-dark-border flex items-center justify-between gap-4">
            <div className="flex items-center gap-3 min-w-0">
              <div className="w-10 h-10 rounded-xl bg-blue-50 dark:bg-blue-900/30 flex items-center justify-center shrink-0">
                <LayoutGrid className="w-5 h-5 text-blue-600 dark:text-blue-400" />
              </div>
              <div className="min-w-0">
                <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text truncate">
                  Browse &amp; Connect
                </h3>
                <p className="text-sm text-gray-500 dark:text-dark-muted truncate">
                  Search {available.length} available {available.length === 1 ? 'integration' : 'integrations'}.
                </p>
              </div>
            </div>
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors p-1 rounded-lg hover:bg-gray-100 dark:hover:bg-dark-border/50 shrink-0"
              aria-label="Close"
            >
              <X className="w-6 h-6" />
            </button>
          </div>

          {/* Search bar */}
          <div className="px-5 pt-4">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400 pointer-events-none" />
              <input
                ref={inputRef}
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search by name, description, or category…"
                className="w-full pl-11 pr-4 py-2.5 rounded-xl border border-gray-200 dark:border-dark-border bg-gray-50 dark:bg-dark-bg text-gray-900 dark:text-dark-text placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500 transition"
              />
            </div>
          </div>

          {/* Body: category sidebar + grid */}
          <div className="flex-1 overflow-hidden flex">
            {/* Category sidebar */}
            {categories.length > 0 && (
              <aside className="hidden sm:block w-48 shrink-0 border-r border-gray-100 dark:border-dark-border overflow-y-auto p-3">
                <p className="px-2 pb-2 text-[11px] font-semibold uppercase tracking-wider text-gray-400 dark:text-dark-muted">
                  Categories
                </p>
                <ul className="space-y-1">
                  <li>
                    <button
                      onClick={() => setSelectedCategory(null)}
                      className={`w-full text-left px-3 py-1.5 rounded-lg text-sm transition-colors ${
                        selectedCategory === null
                          ? 'bg-gray-900 text-white dark:bg-white dark:text-gray-900 font-semibold'
                          : 'text-gray-600 dark:text-dark-muted hover:bg-gray-100 dark:hover:bg-dark-border/50'
                      }`}
                    >
                      All
                      <span className="float-right opacity-70 text-xs">{available.length}</span>
                    </button>
                  </li>
                  {categories.map((category) => {
                    const count = available.filter((i) =>
                      (i.categories?.length ? i.categories : [UNCATEGORIZED]).includes(category)
                    ).length;
                    return (
                      <li key={category}>
                        <button
                          onClick={() => setSelectedCategory(category)}
                          className={`w-full text-left px-3 py-1.5 rounded-lg text-sm transition-colors ${
                            selectedCategory === category
                              ? 'bg-blue-600 text-white font-semibold'
                              : 'text-gray-600 dark:text-dark-muted hover:bg-gray-100 dark:hover:bg-dark-border/50'
                          }`}
                        >
                          <span className="truncate inline-block max-w-[8rem] align-bottom">{category}</span>
                          <span className="float-right opacity-70 text-xs">{count}</span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </aside>
            )}

            {/* Mobile category chips (only when no sidebar) */}
            {categories.length > 0 && (
              <div className="sm:hidden flex flex-wrap gap-2 px-5 py-3 border-b border-gray-100 dark:border-dark-border overflow-x-auto">
                <button
                  onClick={() => setSelectedCategory(null)}
                  className={`px-3 py-1 text-xs rounded-full whitespace-nowrap ${
                    selectedCategory === null
                      ? 'bg-gray-900 text-white dark:bg-white dark:text-gray-900'
                      : 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300'
                  }`}
                >
                  All
                </button>
                {categories.map((category) => (
                  <button
                    key={category}
                    onClick={() => setSelectedCategory(category)}
                    className={`px-3 py-1 text-xs rounded-full whitespace-nowrap ${
                      selectedCategory === category
                        ? 'bg-blue-600 text-white'
                        : 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300'
                    }`}
                  >
                    {category}
                  </button>
                ))}
              </div>
            )}

            {/* Grid */}
            <div className="flex-1 overflow-y-auto p-5">
              {filtered.length === 0 ? (
                <div className="h-full flex flex-col items-center justify-center text-center text-gray-500 dark:text-dark-muted py-16">
                  <Search className="w-10 h-10 mb-3 opacity-40" />
                  <p className="font-medium">No integrations found</p>
                  <p className="text-sm">Try a different search or category.</p>
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                  {filtered.map((integration) => {
                    const isConnected = connectedDomains?.has(integration.domain);
                    return (
                      <div
                        key={integration.domain}
                        className="relative rounded-xl border border-gray-200 dark:border-dark-border bg-white dark:bg-dark-surface p-4 shadow-sm flex flex-col justify-between hover:shadow-md hover:border-blue-300 dark:hover:border-blue-700 transition-all"
                      >
                        <div className="mb-2">
                          <div className="flex items-start justify-between gap-2">
                            <div className="flex items-center gap-2 min-w-0">
                              <h4 className="text-base font-bold text-gray-900 dark:text-white leading-tight truncate">
                                {integration.name}
                              </h4>
                              {getAccessIcon(integration.access_type)}
                            </div>
                            {isConnected && (
                              <span className="shrink-0 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-green-700 bg-green-100 dark:bg-green-900/30 dark:text-green-400 rounded-full">
                                Connected
                              </span>
                            )}
                          </div>
                          <span className="inline-block mt-1 px-2 py-0.5 text-[10px] font-semibold tracking-wide text-gray-500 bg-gray-100 dark:bg-gray-800 dark:text-gray-400 rounded">
                            {integration.author === 'Core' ? 'OFFICIAL' : 'COMMUNITY'}
                          </span>
                        </div>

                        <p
                          className="text-sm text-gray-600 dark:text-gray-300 mt-1 mb-4 line-clamp-2"
                          title={integration.description}
                        >
                          {integration.description || 'No description provided.'}
                        </p>

                        <div className="flex items-center justify-between pt-3 border-t border-gray-100 dark:border-dark-border mt-auto">
                          <div className="text-xs text-gray-400 dark:text-gray-500">v{integration.version}</div>
                          <div className="flex space-x-2">
                            <button
                              onClick={() => onDocs(integration.domain)}
                              className="inline-flex items-center px-2.5 py-1.5 border border-gray-300 dark:border-gray-600 rounded-lg text-xs font-medium text-gray-700 dark:text-gray-200 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700 focus:outline-none cursor-pointer"
                              title="Documentation"
                            >
                              <FileText className="h-3.5 w-3.5 mr-1" aria-hidden="true" />
                              Docs
                            </button>
                            <button
                              onClick={() => onAdd(integration.domain)}
                              className="inline-flex items-center px-2.5 py-1.5 border border-transparent rounded-lg text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 focus:outline-none cursor-pointer"
                              title="Connect"
                            >
                              <Plus className="h-3.5 w-3.5 mr-1" aria-hidden="true" />
                              Add
                            </button>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </Portal>
  );
};

export default BrowseIntegrationsModal;
