import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
  Activity,
  ChevronLeft,
  RotateCcw,
  Minus,
  Plus,
  Crosshair,
  ExternalLink,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Modal } from '../ui/Modal';
import { AnatomyGraphView } from './AnatomyGraphView';
import { anatomyService } from '../../services/anatomyService';
import type {
  AnatomyStructure,
  AnatomyGraphResponse,
  AnatomyGraphNodeItem,
  AnatomyCategory,
} from '../../types/anatomy';
import { CATEGORY_COLORS, CATEGORY_LABELS } from '../../types/anatomy';

const MAX_DEPTH = 3;

interface Props {
  isOpen: boolean;
  onClose: () => void;
  initialStructure: AnatomyStructure;
  /** When provided, an "Open" button navigates the host page to the selected node. */
  onNavigate?: (structure: AnatomyStructure) => void;
}

export const AnatomyGraphModal: React.FC<Props> = ({
  isOpen,
  onClose,
  initialStructure,
  onNavigate,
}) => {
  const { t } = useTranslation();
  const [rootStructure, setRootStructure] = useState<AnatomyStructure>(initialStructure);
  const [graph, setGraph] = useState<AnatomyGraphResponse | null>(null);
  const [depth, setDepth] = useState(1);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [hiddenCats, setHiddenCats] = useState<Set<AnatomyCategory>>(new Set());
  const [history, setHistory] = useState<AnatomyStructure[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  // Reset everything when the modal is (re)opened.
  useEffect(() => {
    if (isOpen) {
      setRootStructure(initialStructure);
      setHistory([]);
      setSelectedNodeId(initialStructure.id);
      setHiddenCats(new Set());
    }
  }, [isOpen, initialStructure]);

  // Fetch graph whenever root or depth changes.
  useEffect(() => {
    if (!isOpen) return;
    let mounted = true;
    setIsLoading(true);
    anatomyService
      .getGraph(rootStructure.slug, depth)
      .then((data) => {
        if (!mounted) return;
        setGraph(data);
        setSelectedNodeId(rootStructure.id);
      })
      .catch((err) => {
        console.error('Failed to fetch anatomy graph:', err);
        if (mounted) setGraph(null);
      })
      .finally(() => mounted && setIsLoading(false));
    return () => {
      mounted = false;
    };
  }, [isOpen, rootStructure, depth]);

  const nodes = useMemo(() => graph?.nodes ?? [], [graph]);
  const edges = useMemo(() => graph?.edges ?? [], [graph]);

  const selectedNode: AnatomyGraphNodeItem | null = useMemo(
    () => nodes.find((n) => n.id === selectedNodeId) ?? null,
    [nodes, selectedNodeId]
  );

  // Categories present in the current graph (drives the legend).
  const presentCategories = useMemo(() => {
    const set = new Set<AnatomyCategory>();
    nodes.forEach((n) => set.add(n.category));
    return Array.from(set);
  }, [nodes]);

  const handleFocusNode = useCallback(
    (id: string) => {
      const target = nodes.find((n) => n.id === id);
      if (!target || id === rootStructure.id) return;
      setHistory((h) => [...h, rootStructure]);
      setRootStructure(target);
    },
    [nodes, rootStructure]
  );

  const handleBack = useCallback(() => {
    setHistory((h) => {
      if (h.length === 0) return h;
      const prev = h[h.length - 1];
      setRootStructure(prev);
      return h.slice(0, -1);
    });
  }, []);

  const handleReset = useCallback(() => {
    setHistory([]);
    setRootStructure(initialStructure);
  }, [initialStructure]);

  const handleOpenSelected = useCallback(() => {
    if (!selectedNode || !onNavigate) return;
    onNavigate(selectedNode);
    onClose();
  }, [selectedNode, onNavigate, onClose]);

  const toggleCategory = (cat: AnatomyCategory) => {
    setHiddenCats((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  };

  const canGoBack = history.length > 0;
  const hiddenArray = useMemo(() => Array.from(hiddenCats), [hiddenCats]);

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={t('anatomy.relationships_title', { name: rootStructure.name })}
      className="max-w-6xl"
    >
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-2 flex-wrap mb-3">
        <div className="flex items-center gap-1.5">
          {/* Back */}
          <button
            onClick={handleBack}
            disabled={!canGoBack}
            title={t('anatomy.graph_back')}
            className="flex items-center gap-1 px-2 py-1.5 rounded-lg text-[10px] font-black uppercase tracking-widest bg-gray-100 dark:bg-dark-bg text-gray-600 dark:text-dark-muted hover:bg-gray-200 dark:hover:bg-dark-border transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <ChevronLeft className="w-3.5 h-3.5" />
            {t('anatomy.graph_back')}
          </button>
          {/* Reset */}
          <button
            onClick={handleReset}
            disabled={rootStructure.id === initialStructure.id}
            title={t('anatomy.graph_reset')}
            className="flex items-center gap-1 px-2 py-1.5 rounded-lg text-[10px] font-black uppercase tracking-widest bg-gray-100 dark:bg-dark-bg text-gray-600 dark:text-dark-muted hover:bg-gray-200 dark:hover:bg-dark-border transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            {t('anatomy.graph_reset')}
          </button>
        </div>

        {/* Depth stepper */}
        <div className="flex items-center gap-1.5 bg-gray-100 dark:bg-dark-bg rounded-lg px-1.5 py-1">
          <span className="text-[10px] font-black uppercase tracking-widest text-gray-400 ml-1">
            {t('anatomy.graph_depth')}
          </span>
          <button
            onClick={() => setDepth((d) => Math.max(1, d - 1))}
            disabled={depth <= 1}
            className="p-1 rounded-md bg-white dark:bg-dark-surface text-gray-600 dark:text-dark-muted hover:text-blue-500 disabled:opacity-30 transition-colors"
          >
            <Minus className="w-3 h-3" />
          </button>
          <span className="text-xs font-black text-gray-700 dark:text-dark-text w-4 text-center">
            {depth}
          </span>
          <button
            onClick={() => setDepth((d) => Math.min(MAX_DEPTH, d + 1))}
            disabled={depth >= MAX_DEPTH}
            className="p-1 rounded-md bg-white dark:bg-dark-surface text-gray-600 dark:text-dark-muted hover:text-blue-500 disabled:opacity-30 transition-colors"
          >
            <Plus className="w-3 h-3" />
          </button>
        </div>
      </div>

      {/* Graph canvas */}
      <div className="relative h-[58vh] rounded-xl overflow-hidden border border-gray-100 dark:border-dark-border bg-gray-50 dark:bg-dark-bg">
        {isLoading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/50 dark:bg-dark-surface/50 backdrop-blur-sm">
            <Activity className="w-7 h-7 text-blue-500 animate-spin" />
          </div>
        )}

        <AnatomyGraphView
          rootId={rootStructure.id}
          nodes={nodes}
          edges={edges}
          selectedNodeId={selectedNodeId ?? undefined}
          hiddenCategories={hiddenArray}
          onSelectNode={setSelectedNodeId}
          onFocusNode={handleFocusNode}
        />

        {/* Floating inspector for the selected node */}
        {selectedNode && !isLoading && (
          <div className="absolute bottom-3 left-3 z-20 bg-white/95 dark:bg-dark-surface/95 backdrop-blur-sm rounded-xl shadow-lg border border-gray-100 dark:border-dark-border px-3 py-2.5 max-w-[260px]">
            <div className="flex items-center gap-2 min-w-0">
              <span
                className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                style={{ background: CATEGORY_COLORS[selectedNode.category] }}
              />
              <span className="text-sm font-bold text-gray-800 dark:text-dark-text truncate">
                {selectedNode.name}
              </span>
              {selectedNode.id !== rootStructure.id && (
                <span className="text-[9px] font-black uppercase tracking-wider text-gray-400 flex-shrink-0">
                  L{selectedNode.depth}
                </span>
              )}
            </div>
            <div className="flex items-center gap-1.5 mt-2">
              {selectedNode.id !== rootStructure.id && (
                <button
                  onClick={() => handleFocusNode(selectedNode.id)}
                  className="flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-black uppercase tracking-widest bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 hover:bg-blue-100 dark:hover:bg-blue-900/40 transition-colors"
                >
                  <Crosshair className="w-3 h-3" />
                  {t('anatomy.graph_focus')}
                </button>
              )}
              {onNavigate && (
                <button
                  onClick={handleOpenSelected}
                  className="flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-black uppercase tracking-widest text-white transition-colors"
                  style={{ background: CATEGORY_COLORS[selectedNode.category] }}
                >
                  <ExternalLink className="w-3 h-3" />
                  {t('anatomy.graph_open')}
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Legend + hint */}
      <div className="mt-3 flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-1.5 flex-wrap">
          {presentCategories.map((cat) => {
            const hidden = hiddenCats.has(cat);
            return (
              <button
                key={cat}
                onClick={() => toggleCategory(cat)}
                title={t(`anatomy.categories.${cat}`)}
                className={`flex items-center gap-1.5 px-2 py-1 rounded-full text-[10px] font-bold transition-all ${
                  hidden
                    ? 'bg-gray-100 dark:bg-dark-bg text-gray-300 dark:text-dark-border line-through'
                    : 'bg-gray-100 dark:bg-dark-bg text-gray-600 dark:text-dark-muted'
                }`}
              >
                <span
                  className="w-2 h-2 rounded-full"
                  style={{
                    background: CATEGORY_COLORS[cat],
                    opacity: hidden ? 0.3 : 1,
                  }}
                />
                {CATEGORY_LABELS[cat]}
              </button>
            );
          })}
        </div>
        <p className="text-[10px] text-gray-400 dark:text-dark-muted">
          {t('anatomy.graph_inspect_hint')}
        </p>
      </div>
    </Modal>
  );
};
