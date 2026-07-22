import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangle,
  Save,
  Network,
  Trash2,
  Plus,
  RefreshCw,
  Share2,
  ArrowRight,
} from 'lucide-react';
import { TaskInfo } from '../../../../types/ai';
import { HitlHandlerProps } from '../registry';
import { OutcomeDetailModal } from '../outcome';
import {
  AnatomyImportNode,
  AnatomyImportEdge,
  AnatomyRelationType,
  RELATION_LABELS,
} from '../../../../types/anatomy';
import { anatomyService } from '../../../../services/anatomyService';
import {
  getAIAssistance,
  resolveHitlTask,
} from '../../../../services/aiAssistanceService';
import {
  ConceptGraphView,
  type ConceptGraphNode,
  type ConceptGraphEdgeData,
} from '../../../ui/ConceptGraphView';

const EXISTING_NODE_COLOR = '#94a3b8'; // slate-400 — "already in the catalog"
const NEW_NODE_COLOR = '#6366f1'; // indigo-500 — "will be added"

const RELATION_TYPES = Object.keys(RELATION_LABELS) as AnatomyRelationType[];

/** Compact existing-graph snapshot embedded by the backend's pre-flight
 * search (root + node slugs + edges). Passes through to the generation prompt
 * so the LLM fills gaps rather than duplicating. */
interface GraphDraftSnapshot {
  root_slug?: string;
  root_name?: string;
  node_count?: number;
  edge_count?: number;
  node_slugs?: string[];
}

/** Shape of the editable draft carried through to import. */
interface GraphDraft {
  nodes: AnatomyImportNode[];
  edges: AnatomyImportEdge[];
}

/** Parse the LLM's suggested_data into a clean editable draft. */
function suggestedToDraft(suggested: unknown): GraphDraft {
  const s = (suggested || {}) as Record<string, unknown>;
  const rawNodes = Array.isArray(s.nodes) ? s.nodes : [];
  const rawEdges = Array.isArray(s.edges) ? s.edges : [];
  const nodes: AnatomyImportNode[] = rawNodes.map((n: any) => ({
    slug: String(n?.slug ?? ''),
    name: String(n?.name ?? ''),
    class_concept_slug: n?.class_concept_slug ? String(n.class_concept_slug) : null,
    standard_system: n?.standard_system ?? null,
    standard_code: n?.standard_code ? String(n.standard_code) : null,
    description: n?.description ? String(n.description) : null,
    is_custom: true,
  }));
  const edges: AnatomyImportEdge[] = rawEdges
    .map((e: any) => {
      const rel = String(e?.relation_type ?? '');
      const relation_type: AnatomyRelationType = (
        RELATION_LABELS[rel as AnatomyRelationType] ? rel : 'PART_OF'
      ) as AnatomyRelationType;
      return {
        source_slug: String(e?.source_slug ?? ''),
        target_slug: String(e?.target_slug ?? ''),
        relation_type,
      };
    })
    .filter((e) => e.source_slug && e.target_slug);
  return { nodes, edges };
}

/** Compact, read-only summary rendered in the chat card body (pre-generation). */
export function renderAnatomyGraphSummary(task: TaskInfo): React.ReactNode {
  const p = task.proposed_payload || {};
  const target = p.target_structure ? String(p.target_structure) : '';
  const chips: { icon: React.ComponentType<{ className?: string }>; label: string }[] = [];
  if (target) chips.push({ icon: Network, label: target });
  // Existing-graph snapshot from the tool's pre-flight search (when present).
  const existing = p.existing as { node_count?: number; edge_count?: number; root_name?: string } | undefined;
  if (existing && typeof existing.node_count === 'number') {
    chips.push({ icon: Network, label: `${existing.node_count} exist` });
    if (typeof existing.edge_count === 'number') {
      chips.push({ icon: Network, label: `${existing.edge_count} edges` });
    }
  }
  // After resolution, the final_payload carries the imported graph counts.
  const nodeCount =
    (task.resolved?.final_payload?.nodes as unknown[] | undefined)?.length ??
    (p.nodes as unknown[] | undefined)?.length;
  const edgeCount =
    (task.resolved?.final_payload?.edges as unknown[] | undefined)?.length ??
    (p.edges as unknown[] | undefined)?.length;
  if (typeof nodeCount === 'number')
    chips.push({ icon: Network, label: `${nodeCount} nodes` });
  if (typeof edgeCount === 'number')
    chips.push({ icon: Network, label: `${edgeCount} edges` });

  return (
    <div className="flex flex-wrap gap-1.5">
      {chips.length === 0 ? null : (
        chips.map((c, i) => (
          <span
            key={i}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border text-[10px] font-bold text-gray-600 dark:text-dark-text"
          >
            <c.icon className="w-2.5 h-2.5 text-indigo-500 dark:text-indigo-400" />
            <span className="truncate max-w-[160px]">{c.label}</span>
          </span>
        ))
      )}
    </div>
  );
}

/** Result details rendered inside the RESOLVED card. The node/edge count chips
 *  are clickable and open a popup listing exactly which items were imported,
 *  with an added/updated breakdown from the import stats. */
const AnatomyOutcomeDetail: React.FC<{ task: TaskInfo }> = ({ task }) => {
  const { t } = useTranslation();
  const [view, setView] = useState<null | 'nodes' | 'edges'>(null);

  const stats = task.resolved?.result?.stats as Record<string, number> | undefined;
  const nodes = (task.resolved?.final_payload?.nodes as AnatomyImportNode[] | undefined) ?? [];
  const edges = (task.resolved?.final_payload?.edges as AnatomyImportEdge[] | undefined) ?? [];
  const num = (v: unknown) => (typeof v === 'number' ? v : Number(v) || 0);
  const added = num(stats?.nodes_added);
  const updated = num(stats?.nodes_updated);
  const eAdded = num(stats?.edges_added);
  const eUpdated = num(stats?.edges_updated);
  const errors = num(stats?.errors);

  const nodeTotal = nodes.length || added + updated;
  const edgeTotal = edges.length || eAdded + eUpdated;
  if (!nodeTotal && !edgeTotal && !errors) return null;

  const nodeSub = `${added} added · ${updated} updated`;
  const edgeSub = `${eAdded} added · ${eUpdated} updated`;
  const chipBtn =
    'inline-flex items-center gap-1 px-2 py-0.5 rounded-full border border-gray-200 dark:border-dark-border bg-gray-50 dark:bg-dark-bg text-[10px] font-bold text-gray-600 dark:text-dark-text hover:bg-indigo-50 dark:hover:bg-indigo-900/20 hover:border-indigo-200 dark:hover:border-indigo-500/40 transition-colors cursor-pointer';

  return (
    <>
      <div className="flex flex-wrap gap-1.5 mt-2 pt-2 border-t border-gray-100 dark:border-dark-border">
        {nodeTotal > 0 && (
          <button type="button" onClick={() => setView('nodes')} className={chipBtn}>
            <Network className="w-2.5 h-2.5 text-indigo-500 dark:text-indigo-400" />
            <span>{nodeTotal} {t('ai_chat.hitl.generate_anatomy_graph.nodes', 'Nodes')}</span>
          </button>
        )}
        {edgeTotal > 0 && (
          <button type="button" onClick={() => setView('edges')} className={chipBtn}>
            <Share2 className="w-2.5 h-2.5 text-indigo-500 dark:text-indigo-400" />
            <span>{edgeTotal} {t('ai_chat.hitl.generate_anatomy_graph.edges', 'Edges')}</span>
          </button>
        )}
        {errors > 0 && (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border border-rose-200 dark:border-rose-500/30 bg-rose-50 dark:bg-rose-900/20 text-[10px] font-bold text-rose-600 dark:text-rose-400">
            <AlertTriangle className="w-2.5 h-2.5 text-rose-500" />
            <span>{errors} errors</span>
          </span>
        )}
      </div>

      <OutcomeDetailModal
        isOpen={view === 'nodes'}
        onClose={() => setView(null)}
        title={t('ai_chat.hitl.generate_anatomy_graph.nodes', 'Nodes')}
        subtitle={nodeSub}
        icon={Network}
      >
        {nodes.length === 0 ? (
          <p className="text-xs text-gray-400">{t('ai_chat.hitl.generate_anatomy_graph.empty', 'No items.')}</p>
        ) : (
          <ul className="space-y-1.5">
            {nodes.map((n, i) => (
              <li key={`${n.slug}-${i}`} className="flex items-center gap-2 text-xs">
                <span className="font-semibold text-gray-800 dark:text-dark-text truncate">
                  {n.name || n.slug}
                </span>
                {n.slug && n.slug !== n.name && (
                  <span className="text-gray-400 dark:text-dark-muted truncate">/{n.slug}</span>
                )}
                {n.class_concept_slug && (
                  <span className="ml-auto inline-flex items-center px-1.5 py-0.5 rounded bg-gray-100 dark:bg-dark-bg text-[10px] font-bold text-gray-500 dark:text-dark-muted truncate">
                    {n.class_concept_slug}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </OutcomeDetailModal>

      <OutcomeDetailModal
        isOpen={view === 'edges'}
        onClose={() => setView(null)}
        title={t('ai_chat.hitl.generate_anatomy_graph.edges', 'Edges')}
        subtitle={edgeSub}
        icon={Share2}
      >
        {edges.length === 0 ? (
          <p className="text-xs text-gray-400">{t('ai_chat.hitl.generate_anatomy_graph.empty', 'No items.')}</p>
        ) : (
          <ul className="space-y-1.5">
            {edges.map((e, i) => (
              <li key={`${e.source_slug}-${e.target_slug}-${e.relation_type}-${i}`} className="flex items-center gap-2 text-xs min-w-0">
                <span className="font-semibold text-gray-800 dark:text-dark-text truncate">{e.source_slug}</span>
                <ArrowRight className="w-3 h-3 text-gray-400 flex-shrink-0" />
                <span className="font-semibold text-gray-800 dark:text-dark-text truncate">{e.target_slug}</span>
                <span className="ml-auto inline-flex items-center px-1.5 py-0.5 rounded bg-gray-100 dark:bg-dark-bg text-[10px] font-bold text-gray-500 dark:text-dark-muted truncate flex-shrink-0">
                  {RELATION_LABELS[e.relation_type] ?? e.relation_type}
                </span>
              </li>
            ))}
          </ul>
        )}
      </OutcomeDetailModal>
    </>
  );
};

export function renderAnatomyGraphOutcome(task: TaskInfo): React.ReactNode | null {
  return <AnatomyOutcomeDetail task={task} />;
}

export const GenerateAnatomyGraphHandler: React.FC<HitlHandlerProps> = ({
  task,
  sessionId,
  onResolved,
  onCancel,
}) => {
  const { t } = useTranslation();
  const targetStructure = String(task.proposed_payload?.target_structure ?? '').trim();
  const existing = task.proposed_payload?.existing as GraphDraftSnapshot | undefined;
  const preGenerated = task.proposed_payload?.generated as { nodes?: unknown[]; edges?: unknown[] } | undefined;

  const [draft, setDraft] = useState<GraphDraft | null>(() => {
    // Lazy init from the proposal payload — if generation ran at proposal time
    // (schema v3), the modal opens instantly with the prefilled draft. No LLM
    // call, no spinner. The fallback path (generation failed at proposal time)
    // leaves draft null and the effect below calls getAIAssistance client-side.
    if (preGenerated && Array.isArray(preGenerated.nodes) && preGenerated.nodes.length > 0) {
      return suggestedToDraft(preGenerated);
    }
    return null;
  });
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [view, setView] = useState<'graph' | 'table'>('graph');

  // 1. Generate the graph client-side ONLY when no pre-generated payload was
  //    embedded by the tool (or on retry). When the tool already generated at
  //    proposal time, the lazy initializer above populated the draft and this
  //    effect early-returns — the modal opens with zero LLM latency.
  //    `t` + `draft` are intentionally excluded from deps — a language switch
  //    or user edit must NOT re-trigger a paid LLM generation.
  useEffect(() => {
    if (!targetStructure) {
      setGenError(
        t('ai_chat.hitl.generate_anatomy_graph.error_no_target', 'No target structure was provided.'),
      );
      return;
    }
    // attempt === 0 + draft already set ⇒ pre-generated payload, skip the call.
    if (attempt === 0 && draft) return;
    let cancelled = false;
    const run = async () => {
      setGenerating(true);
      setGenError(null);
      try {
        const res = await getAIAssistance({
          task_type: 'define_anatomy_graph',
          user_input: targetStructure,
          context: existing ? { existing } : undefined,
        });
        if (cancelled) return;
        if (!res.success) {
          setGenError(
            res.message ||
              t('ai_chat.hitl.generate_anatomy_graph.error_generic', 'Generation failed.'),
          );
          return;
        }
        setDraft(suggestedToDraft(res.suggested_data));
      } catch (e: any) {
        if (cancelled) return;
        const msg =
          e?.response?.data?.detail ||
          e?.message ||
          t('ai_chat.hitl.generate_anatomy_graph.error_generic', 'Generation failed.');
        setGenError(typeof msg === 'string' ? msg : JSON.stringify(msg));
      } finally {
        if (!cancelled) setGenerating(false);
      }
    };
    run();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetStructure, attempt]);

  const handleRetry = () => {
    setDraft(null);
    setGenError(null);
    setAttempt((n) => n + 1);
  };

  // --- node/edge mutators ---
  const patchNode = (idx: number, patch: Partial<AnatomyImportNode>) =>
    setDraft((prev) =>
      prev
        ? {
            ...prev,
            nodes: prev.nodes.map((n, i) => (i === idx ? { ...n, ...patch } : n)),
          }
        : prev,
    );

  const removeNode = (idx: number) =>
    setDraft((prev) =>
      prev ? { ...prev, nodes: prev.nodes.filter((_, i) => i !== idx) } : prev,
    );

  const addNode = () =>
    setDraft(
      (prev) =>
        prev
          ? {
              ...prev,
              nodes: [
                ...prev.nodes,
                {
                  slug: '',
                  name: '',
                  class_concept_slug: 'organ',
                  is_custom: true,
                },
              ],
            }
          : prev,
    );

  const patchEdge = (idx: number, patch: Partial<AnatomyImportEdge>) =>
    setDraft((prev) =>
      prev
        ? {
            ...prev,
            edges: prev.edges.map((e, i) => (i === idx ? { ...e, ...patch } : e)),
          }
        : prev,
    );

  const removeEdge = (idx: number) =>
    setDraft((prev) =>
      prev ? { ...prev, edges: prev.edges.filter((_, i) => i !== idx) } : prev,
    );

  const addEdge = () =>
    setDraft(
      (prev) =>
        prev
          ? {
              ...prev,
              edges: [...prev.edges, { source_slug: '', target_slug: '', relation_type: 'PART_OF' }],
            }
          : prev,
    );

  // 3. Approve: import via the canonical REST endpoint, then resolve.
  const handleConfirm = async () => {
    if (!draft) return;
    setImportError(null);
    setSubmitting(true);
    try {
      const stats = await anatomyService.importGraph(draft);
      if (sessionId) {
        try {
          await resolveHitlTask(sessionId, task.proposal_id, {
            status: 'confirmed',
            final_payload: {
              target_structure: targetStructure,
              nodes: draft.nodes,
              edges: draft.edges,
            },
            result: { stats },
          });
        } catch (resolveErr) {
          // Import succeeded; a failed resolve must not undo it.
          console.error('HITL resolve recording failed (import already committed)', resolveErr);
        }
      }
      onResolved({
        ...task,
        status: 'confirmed',
        resolved: {
          final_payload: {
            target_structure: targetStructure,
            nodes: draft.nodes,
            edges: draft.edges,
          },
          result: { stats },
          at: new Date().toISOString(),
        },
      });
    } catch (e: any) {
      console.error('HITL generate_anatomy_graph import failed', e);
      const status = e?.response?.status;
      if (status === 403) {
        setImportError(
          t(
            'ai_chat.hitl.generate_anatomy_graph.error_403',
            'Only system admins can import anatomy graphs.',
          ),
        );
      } else {
        const msg =
          e?.response?.data?.detail ||
          e?.message ||
          t('ai_chat.hitl.error_generic', 'Failed to save. Please review and try again.');
        setImportError(typeof msg === 'string' ? msg : JSON.stringify(msg));
      }
    } finally {
      setSubmitting(false);
    }
  };

  // 4. Reject: record dismissed, close.
  const handleReject = () => {
    if (submitting) return;
    if (sessionId) {
      resolveHitlTask(sessionId, task.proposal_id, { status: 'dismissed' }).catch((err) =>
        console.error('HITL reject record failed', err),
      );
    }
    onResolved({ ...task, status: 'dismissed', resolved: { at: new Date().toISOString() } });
  };

  const canSubmit =
    !generating && !submitting && !!draft && draft.nodes.length > 0 && draft.nodes.every((n) => n.slug && n.name);
  const error = importError || genError;

  // Map the editable draft → ConceptGraphView props, color-coding nodes that
  // already exist in the catalog (slate) vs new ones the import will add
  // (indigo). Recomputes on every draft edit so the graph stays live.
  const existingSlugs = useMemo(
    () => new Set(existing?.node_slugs ?? []),
    [existing],
  );
  const graphNodes: ConceptGraphNode[] = useMemo(() => {
    if (!draft) return [];
    return draft.nodes.map((n) => ({
      id: n.slug,
      name: n.name,
      primary_kind: n.class_concept_slug || 'organ',
      color: existingSlugs.has(n.slug) ? EXISTING_NODE_COLOR : NEW_NODE_COLOR,
      type: 'anatomy',
    }));
  }, [draft, existingSlugs]);
  const graphEdges: ConceptGraphEdgeData[] = useMemo(() => {
    if (!draft) return [];
    return draft.edges.map((e, i) => ({
      id: `${e.source_slug}-${e.target_slug}-${e.relation_type}-${i}`,
      source: e.source_slug,
      target: e.target_slug,
      relation: e.relation_type,
    }));
  }, [draft]);

  const inputCls =
    'w-full px-2 py-1 text-[12px] rounded-md border border-gray-200 dark:border-dark-border bg-white dark:bg-dark-bg/70 text-gray-800 dark:text-dark-text focus:outline-none focus:ring-2 focus:ring-indigo-400/40';

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {error && (
        <div className="mx-4 mt-4 flex items-start gap-2 rounded-xl border border-rose-200 dark:border-rose-500/30 bg-rose-50 dark:bg-rose-900/10 p-3 text-[11px] text-rose-700 dark:text-rose-300">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
          <span className="break-words">{error}</span>
        </div>
      )}

      <div className="flex-1 min-h-0 overflow-y-auto p-4 custom-scrollbar space-y-4">
        {/* Global-ontology warning */}
        <div className="rounded-lg bg-indigo-50 dark:bg-indigo-900/10 border border-indigo-100 dark:border-indigo-500/20 px-3 py-2 text-[11px] text-indigo-700 dark:text-indigo-300">
          {t(
            'ai_chat.hitl.generate_anatomy_graph.global_note',
            'Imported structures are added to the global anatomy catalog shared across all people.',
          )}
        </div>

        {generating && (
          <div className="flex items-center gap-2 text-[12px] text-gray-500 dark:text-dark-muted py-8 justify-center">
            <RefreshCw className="w-4 h-4 animate-spin" />
            <span>
              {t('ai_chat.hitl.generate_anatomy_graph.generating', 'Generating anatomy graph…')}
            </span>
          </div>
        )}

        {!generating && !draft && !genError && (
          <div className="text-[12px] text-gray-500 dark:text-dark-muted py-8 text-center">
            {t('ai_chat.hitl.generate_anatomy_graph.empty', 'No graph generated yet.')}
          </div>
        )}

        {!generating && !draft && genError && (
          <div className="flex flex-col items-center gap-3 py-8">
            <span className="text-[12px] text-gray-500 dark:text-dark-muted">
              {t('ai_chat.hitl.generate_anatomy_graph.retry_hint', 'Generation failed. You can retry.')}
            </span>
            <button
              type="button"
              onClick={handleRetry}
              className="inline-flex items-center gap-2 px-4 py-2 text-[12px] font-bold text-indigo-600 dark:text-indigo-400 border border-indigo-200 dark:border-indigo-500/30 rounded-lg hover:bg-indigo-50 dark:hover:bg-indigo-900/10 transition-colors"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              {t('ai_chat.hitl.generate_anatomy_graph.retry', 'Retry generation')}
            </button>
          </div>
        )}

        {draft && (
          <>
            {/* Graph / table view toggle */}
            <div className="flex items-center gap-1 rounded-lg bg-gray-100 dark:bg-dark-bg/70 p-0.5 w-fit">
              <button
                type="button"
                onClick={() => setView('graph')}
                className={`px-3 py-1 text-[11px] font-bold rounded-md transition-colors ${
                  view === 'graph'
                    ? 'bg-white dark:bg-dark-bg text-indigo-600 dark:text-indigo-400 shadow-sm'
                    : 'text-gray-500 dark:text-dark-muted'
                }`}
              >
                {t('ai_chat.hitl.generate_anatomy_graph.view_graph', 'Graph')}
              </button>
              <button
                type="button"
                onClick={() => setView('table')}
                className={`px-3 py-1 text-[11px] font-bold rounded-md transition-colors ${
                  view === 'table'
                    ? 'bg-white dark:bg-dark-bg text-indigo-600 dark:text-indigo-400 shadow-sm'
                    : 'text-gray-500 dark:text-dark-muted'
                }`}
              >
                {t('ai_chat.hitl.generate_anatomy_graph.view_table', 'Table')}
              </button>
            </div>
            {/* Legend */}
            <div className="flex items-center gap-4 text-[10px] text-gray-400">
              <span className="flex items-center gap-1">
                <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: NEW_NODE_COLOR }} />
                {t('ai_chat.hitl.generate_anatomy_graph.legend_new', 'New')}
              </span>
              <span className="flex items-center gap-1">
                <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: EXISTING_NODE_COLOR }} />
                {t('ai_chat.hitl.generate_anatomy_graph.legend_existing', 'Already exists')}
              </span>
            </div>

            {/* Graph view (read-only preview, live-updates as the user edits).
                Fixed pixel height — ReactFlow measures the parent's clientHeight
                and the modal's overflow-y-auto body doesn't constrain flex
                children, so flex-1/min-h resolves to 0 (the #004 warning). */}
            {view === 'graph' && graphNodes.length > 0 && (
              <div
                className="rounded-lg border border-gray-200 dark:border-dark-border overflow-hidden bg-gray-50 dark:bg-dark-bg/30"
                style={{ height: 450 }}
              >
                <ConceptGraphView
                  nodes={graphNodes}
                  edges={graphEdges}
                  showMiniMap={false}
                  className="h-full w-full"
                />
              </div>
            )}

            {/* Table view (editable) */}
            {view === 'table' && (
              <>
            {/* Nodes table */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <h4 className="text-[11px] font-bold uppercase tracking-wide text-gray-500 dark:text-dark-muted">
                  {t('ai_chat.hitl.generate_anatomy_graph.nodes', 'Nodes')}{' '}
                  <span className="text-gray-400">({draft.nodes.length})</span>
                </h4>
                <button
                  type="button"
                  onClick={addNode}
                  className="inline-flex items-center gap-1 text-[11px] font-bold text-indigo-600 dark:text-indigo-400 hover:underline"
                >
                  <Plus className="w-3 h-3" />
                  {t('common.add', 'Add')}
                </button>
              </div>
              <div className="space-y-1.5">
                <div className="grid grid-cols-[1fr_1fr_1fr_auto] gap-1.5 px-1 text-[10px] font-bold uppercase tracking-wide text-gray-400 dark:text-dark-muted">
                  <span>{t('ai_chat.hitl.generate_anatomy_graph.slug', 'Slug')}</span>
                  <span>{t('ai_chat.hitl.generate_anatomy_graph.name', 'Name')}</span>
                  <span>{t('ai_chat.hitl.generate_anatomy_graph.class', 'Class')}</span>
                  <span />
                </div>
                {draft.nodes.map((n, i) => (
                  <div key={i} className="grid grid-cols-[1fr_1fr_1fr_auto] gap-1.5 items-center">
                    <input
                      className={inputCls}
                      value={n.slug}
                      onChange={(e) => patchNode(i, { slug: e.target.value })}
                      placeholder="left-ventricle"
                    />
                    <input
                      className={inputCls}
                      value={n.name}
                      onChange={(e) => patchNode(i, { name: e.target.value })}
                      placeholder="Left ventricle"
                    />
                    <input
                      className={inputCls}
                      value={n.class_concept_slug ?? ''}
                      onChange={(e) => patchNode(i, { class_concept_slug: e.target.value || null })}
                      placeholder="organ"
                    />
                    <button
                      type="button"
                      onClick={() => removeNode(i)}
                      className="p-1 text-gray-400 hover:text-rose-500 transition-colors"
                      title={t('common.delete', 'Delete')}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            </div>

            {/* Edges table */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <h4 className="text-[11px] font-bold uppercase tracking-wide text-gray-500 dark:text-dark-muted">
                  {t('ai_chat.hitl.generate_anatomy_graph.edges', 'Edges')}{' '}
                  <span className="text-gray-400">({draft.edges.length})</span>
                </h4>
                <button
                  type="button"
                  onClick={addEdge}
                  className="inline-flex items-center gap-1 text-[11px] font-bold text-indigo-600 dark:text-indigo-400 hover:underline"
                >
                  <Plus className="w-3 h-3" />
                  {t('common.add', 'Add')}
                </button>
              </div>
              <div className="space-y-1.5">
                <div className="grid grid-cols-[1fr_1fr_1fr_auto] gap-1.5 px-1 text-[10px] font-bold uppercase tracking-wide text-gray-400 dark:text-dark-muted">
                  <span>{t('ai_chat.hitl.generate_anatomy_graph.source', 'Source')}</span>
                  <span>{t('ai_chat.hitl.generate_anatomy_graph.target', 'Target')}</span>
                  <span>{t('ai_chat.hitl.generate_anatomy_graph.relation', 'Relation')}</span>
                  <span />
                </div>
                {draft.edges.length === 0 && (
                  <p className="text-[11px] text-gray-400 px-1">
                    {t('ai_chat.hitl.generate_anatomy_graph.no_edges', 'No edges.')}
                  </p>
                )}
                {draft.edges.map((e, i) => (
                  <div key={i} className="grid grid-cols-[1fr_1fr_1fr_auto] gap-1.5 items-center">
                    <input
                      className={inputCls}
                      value={e.source_slug}
                      onChange={(ev) => patchEdge(i, { source_slug: ev.target.value })}
                      placeholder="left-ventricle"
                    />
                    <input
                      className={inputCls}
                      value={e.target_slug}
                      onChange={(ev) => patchEdge(i, { target_slug: ev.target.value })}
                      placeholder="heart"
                    />
                    <select
                      className={inputCls}
                      value={e.relation_type}
                      onChange={(ev) =>
                        patchEdge(i, { relation_type: ev.target.value as AnatomyRelationType })
                      }
                    >
                      {RELATION_TYPES.map((r) => (
                        <option key={r} value={r}>
                          {RELATION_LABELS[r]}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      onClick={() => removeEdge(i)}
                      className="p-1 text-gray-400 hover:text-rose-500 transition-colors"
                      title={t('common.delete', 'Delete')}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
              </>
            )}
          </>
        )}
      </div>

      <div className="px-4 py-3 bg-gray-50 dark:bg-dark-bg/50 border-t border-gray-100 dark:border-dark-border flex items-center shrink-0">
        <button
          type="button"
          onClick={handleReject}
          disabled={submitting}
          className="px-5 py-2.5 text-sm font-bold text-rose-600 hover:text-rose-700 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-900/20 rounded-xl transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {t('ai_chat.hitl.reject', 'Reject')}
        </button>
        <div className="ml-auto flex items-center space-x-3">
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="px-5 py-2.5 text-sm font-bold text-gray-500 hover:text-gray-700 dark:text-dark-muted transition-colors disabled:opacity-50"
          >
            {t('common.cancel', 'Cancel')}
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={!canSubmit}
            className="px-6 py-2.5 bg-indigo-600 text-white rounded-xl hover:bg-indigo-700 transition-all font-bold flex items-center justify-center space-x-2 shadow-lg shadow-indigo-200/50 dark:shadow-none disabled:opacity-50 active:scale-95"
          >
            {submitting ? (
              <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
            ) : (
              <Save className="w-4 h-4" />
            )}
            <span>
              {submitting
                ? t('common.saving', 'Saving…')
                : t('ai_chat.hitl.generate_anatomy_graph.confirm', 'Confirm & Import Graph')}
            </span>
          </button>
        </div>
      </div>
    </div>
  );
};
