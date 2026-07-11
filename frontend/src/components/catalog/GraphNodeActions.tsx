/**
 * Shared action buttons for a graph node — "Open in catalog", "Open in domain"
 * (when a dedicated page exists), and "Focus" (center the graph).
 *
 * Consumed by both the {@link GraphNodeDetail} floating card (Phase 2) and the
 * right-click context menu (Phase 3) so there is a single source of truth for
 * the node actions.
 */
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Database, ExternalLink, Crosshair } from 'lucide-react';
import { domainRouteForType } from '../../utils/domainRoute';
import type { CatalogType } from '../../types/catalog';

interface GraphNodeActionsProps {
  type: string;
  id: string;
  /** Preferred for anatomy (the route is ``:slug``); falls back to id. */
  slug?: string;
  /** Centers the graph on this node. Omitted → no Focus button. */
  onFocus?: () => void;
  /** ``'menu'`` renders list-items (for the context menu); ``'toolbar'``
   *  renders inline buttons (for the detail card). */
  variant?: 'toolbar' | 'menu';
}

export const GraphNodeActions: React.FC<GraphNodeActionsProps> = ({
  type,
  id,
  slug,
  onFocus,
  variant = 'toolbar',
}) => {
  const navigate = useNavigate();
  const domainRoute = domainRouteForType(type, id, slug);

  const openInCatalog = () =>
    navigate(`/catalogs?type=${type as CatalogType}&item=${id}`);

  if (variant === 'menu') {
    return (
      <div className="flex flex-col py-1 min-w-[180px]">
        <button
          onClick={openInCatalog}
          className="flex items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-blue-50 dark:hover:bg-blue-900/20 text-gray-700 dark:text-gray-200"
        >
          <Database className="w-3.5 h-3.5 text-blue-500" />
          Open in catalog
        </button>
        {domainRoute && (
          <a
            href={domainRoute}
            className="flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-blue-50 dark:hover:bg-blue-900/20 text-gray-700 dark:text-gray-200"
          >
            <ExternalLink className="w-3.5 h-3.5 text-blue-500" />
            Open in domain
          </a>
        )}
        {onFocus && (
          <button
            onClick={onFocus}
            className="flex items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-blue-50 dark:hover:bg-blue-900/20 text-gray-700 dark:text-gray-200"
          >
            <Crosshair className="w-3.5 h-3.5 text-gray-400" />
            Focus
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="flex items-center gap-1.5">
      <button
        onClick={openInCatalog}
        title="Open in catalog"
        className="flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-md bg-blue-600 text-white hover:bg-blue-700 transition-colors"
      >
        <Database className="w-3 h-3" />
        Catalog
      </button>
      {domainRoute && (
        <a
          href={domainRoute}
          title="Open in domain"
          className="flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-md border border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
        >
          <ExternalLink className="w-3 h-3" />
          Domain
        </a>
      )}
      {onFocus && (
        <button
          onClick={onFocus}
          title="Focus"
          className="flex items-center justify-center w-7 h-7 text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-md transition-colors"
        >
          <Crosshair className="w-3.5 h-3.5" />
        </button>
      )}
    </div>
  );
};
