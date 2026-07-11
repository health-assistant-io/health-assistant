/**
 * Floating detail card shown when a graph node is selected. Rendered inside a
 * ReactFlow ``<NodeToolbar>`` by the custom node type in ``ConceptGraphView``.
 *
 * Shows the node's identity (name, type, kind), its degree (relation count,
 * computed client-side from the loaded edges), and lazily-fetches the full
 * item detail (description/code/aliases) via ``getCatalogItem`` — so the graph
 * payload stays light and detail loads on demand, cached per node so a
 * re-select never refetches.
 */
import React, { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import { DynamicIcon } from '../ui/DynamicIcon';
import { GraphNodeActions } from './GraphNodeActions';
import { getCatalogItem } from '../../services/catalogService';
import {
  CATALOG_TYPE_COLORS,
  CATALOG_TYPE_LABELS,
} from '../../types/concept';

/** The node identity passed in from the graph node data. */
export interface GraphNodeSummary {
  id: string;
  name: string;
  type?: string | null;
  primary_kind?: string | null;
  color?: string | null;
  icon?: { type: string; value: string } | null;
}

interface GraphNodeDetailProps {
  node: GraphNodeSummary;
  /** Number of edges touching this node (client-side degree). */
  degree: number;
  /** Deselect / close the card. */
  onClose?: () => void;
  /** Center the graph on this node. */
  onFocus?: () => void;
}

/** Session-wide cache of lazily-fetched item detail, keyed by `${type}:${id}`.
 *  Prevents refetch on re-select of the same node. */
const detailCache = new Map<string, Record<string, unknown>>();

/** Test-only: clears the lazy-detail cache. */
export function __resetDetailCache() {
  detailCache.clear();
}

function readStr(rec: Record<string, unknown>, key: string): string | null {
  const v = rec[key];
  return typeof v === 'string' && v.trim() ? v.trim() : null;
}

export const GraphNodeDetail: React.FC<GraphNodeDetailProps> = ({
  node,
  degree,
  onClose,
  onFocus,
}) => {
  const cacheKey = `${node.type ?? '_'}:${node.id}`;
  const cached = detailCache.get(cacheKey);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(
    cached ?? null,
  );
  const [loading, setLoading] = useState(!cached);

  useEffect(() => {
    let cancelled = false;
    if (cached) {
      setDetail(cached);
      setLoading(false);
      return;
    }
    if (!node.type) {
      setLoading(false);
      return;
    }
    setLoading(true);
    getCatalogItem(node.type, node.id)
      .then((item) => {
        if (cancelled) return;
        detailCache.set(cacheKey, item);
        setDetail(item);
      })
      .catch(() => {
        if (cancelled) return;
        setDetail(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cacheKey, node.type, node.id]);

  const typeLabel = node.type ? CATALOG_TYPE_LABELS[node.type] ?? node.type : '';
  const typeColor = node.type
    ? CATALOG_TYPE_COLORS[node.type] ?? node.color ?? '#6b7280'
    : node.color ?? '#6b7280';
  const kindLabel = node.primary_kind
    ? String(node.primary_kind).replace(/_/g, ' ')
    : '';
  const description = detail ? readStr(detail, 'description') : null;
  const code = detail ? readStr(detail, 'code') : null;
  const slug = detail ? readStr(detail, 'slug') : null;

  return (
    <div className="w-60 rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 shadow-xl overflow-hidden">
      {/* Header */}
      <div className="flex items-start gap-2 px-3 pt-2.5 pb-2">
        <span
          className="mt-0.5 flex items-center justify-center w-6 h-6 rounded-md shrink-0"
          style={{ backgroundColor: typeColor }}
        >
          <DynamicIcon
            icon={node.icon ? { type: node.icon.type as 'lucide' | 'custom_svg', value: node.icon.value } : null}
            className="w-3.5 h-3.5 text-white"
          />
        </span>
        <p className="flex-1 text-xs font-semibold text-gray-800 dark:text-gray-100 leading-snug break-words">
          {node.name}
        </p>
        {onClose && (
          <button
            onClick={onClose}
            title="Close"
            className="p-0.5 -mt-0.5 -mr-0.5 text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 rounded"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {/* Meta */}
      <div className="flex flex-wrap items-center gap-1.5 px-3 pb-2">
        {typeLabel && (
          <span
            className="px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide rounded text-white"
            style={{ backgroundColor: typeColor }}
          >
            {typeLabel}
          </span>
        )}
        {kindLabel && (
          <span className="text-[10px] text-gray-400 dark:text-gray-500 truncate">
            {kindLabel}
          </span>
        )}
        <span className="text-[10px] text-gray-400 dark:text-gray-500">
          · {degree} {degree === 1 ? 'relation' : 'relations'}
        </span>
      </div>

      {/* Lazy detail */}
      {(loading || description || code) && (
        <div className="px-3 pb-2 space-y-1 border-t border-gray-100 dark:border-gray-700 pt-2">
          {loading && (
            <div className="space-y-1">
              <div className="h-2 bg-gray-100 dark:bg-gray-700 rounded animate-pulse" />
              <div className="h-2 w-2/3 bg-gray-100 dark:bg-gray-700 rounded animate-pulse" />
            </div>
          )}
          {!loading && description && (
            <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-snug line-clamp-3">
              {description}
            </p>
          )}
          {!loading && code && (
            <p className="text-[10px] text-gray-400 dark:text-gray-500">
              code: <span className="font-mono">{code}</span>
            </p>
          )}
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center gap-1.5 px-3 py-2 border-t border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40">
        <GraphNodeActions
          type={node.type ?? ''}
          id={node.id}
          slug={slug ?? undefined}
          onFocus={onFocus}
        />
      </div>
    </div>
  );
};
