import { useState, useEffect, useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Plus, Edit2, Trash2, Save, X, Loader2, Network, List, GitBranch, Link2, Unlink, Maximize2, Download,
} from 'lucide-react';
import PageHeader from '../../components/ui/PageHeader';
import { LoadingState } from '../../components/ui/LoadingState';
import { DynamicIcon, type IconConfig } from '../../components/ui/DynamicIcon';
import { ConceptGraphView, KIND_COLORS, type ConceptGraphNode, type ConceptGraphEdgeData } from '../../components/ui/ConceptGraphView';
import TaxonomyTypeahead from '../../components/ui/TaxonomyTypeahead';
import AnatomyTypeahead, { type AnatomyTypeaheadSelection } from '../../components/ui/AnatomyTypeahead';
import { useUIStore } from '../../store/slices/uiSlice';
import {
  listConcepts, createConcept, updateConcept, deleteConcept,
  getConcept, getConceptNeighbors, createEdge, deleteEdge, listEdges,
} from '../../services/conceptService';
import { anatomyService } from '../../services/anatomyService';
import { downloadSeedsZip } from '../../services/seedService';
import { IconPicker } from '../../components/ui/IconPicker';
import { SearchableDropdown, type DropdownOption } from '../../components/ui/SearchableDropdown';
import type {
  Concept, ConceptKind, ConceptCreateInput, NeighborResult,
} from '../../types/concept';
import { CONCEPT_KIND_LABELS } from '../../types/concept';

const COMMON_COLORS = [
  '#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6',
  '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#6366f1',
];

const MANAGEABLE_KINDS = Object.keys(CONCEPT_KIND_LABELS) as ConceptKind[];

interface FormData {
  name: string;
  slug: string;
  description: string;
  color: string;
  icon: IconConfig;
  aliases: string;
  coding_system: string;
  code: string;
  parent_id: string | null;
  kinds: ConceptKind[];
}

const EMPTY_FORM: FormData = {
  name: '', slug: '', description: '',
  color: '#3b82f6', icon: { type: 'lucide', value: 'Tag' },
  aliases: '', coding_system: '', code: '',
  parent_id: null,
  kinds: [],
};

export default function TaxonomyManager() {
  const { showConfirmation, pageSearchTerm, setPageSearchTerm, setIsPageSearchSupported } = useUIStore();
  const [searchParams, setSearchParams] = useSearchParams();

  // URL-synced state: view mode + active kind
  const [viewMode, setViewModeState] = useState<'list' | 'graph'>(
    (searchParams.get('view') as 'list' | 'graph') || 'list',
  );
  const [activeKind, setActiveKindState] = useState<ConceptKind | 'all'>(
    (searchParams.get('kind') as ConceptKind | 'all') || 'specialty',
  );

  const setViewMode = useCallback((mode: 'list' | 'graph') => {
    setViewModeState(mode);
    setSearchParams(
      (prev) => {
        if (mode === 'list') prev.delete('view');
        else prev.set('view', mode);
        return prev;
      },
      { replace: true },
    );
  }, [setSearchParams]);

  const setActiveKind = useCallback((kind: ConceptKind | 'all') => {
    setActiveKindState(kind);
    setSearchParams(
      (prev) => {
        if (kind === 'specialty') prev.delete('kind');
        else prev.set('kind', kind);
        return prev;
      },
      { replace: true },
    );
  }, [setSearchParams]);
  const [concepts, setConcepts] = useState<Concept[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [downloadingSeeds, setDownloadingSeeds] = useState(false);
  const [isAdding, setIsAdding] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [formData, setFormData] = useState<FormData>(EMPTY_FORM);
  const [slugEdited, setSlugEdited] = useState(false);

  // Parent concept display object (resolved from parent_id when editing).
  const [parentConcept, setParentConcept] = useState<Concept | null>(null);

  // Relationship panel state (shown when editing a concept)
  const [neighbors, setNeighbors] = useState<NeighborResult[]>([]);
  const [neighborsLoading, setNeighborsLoading] = useState(false);
  const [newEdgeTarget, setNewEdgeTarget] = useState<Concept | null>(null);
  const [newEdgeAnatomy, setNewEdgeAnatomy] = useState<AnatomyTypeaheadSelection | null>(null);
  const [newEdgeTargetType, setNewEdgeTargetType] = useState<'concept' | 'anatomy'>('concept');
  const [newEdgeRelation, setNewEdgeRelation] = useState('EXAMINES');
  const [addingEdge, setAddingEdge] = useState(false);

  // Graph view state
  const [graphNodes, setGraphNodes] = useState<ConceptGraphNode[]>([]);
  const [graphEdges, setGraphEdges] = useState<ConceptGraphEdgeData[]>([]);
  const [graphLoading, setGraphLoading] = useState(false);
  const [selectedGraphNode, setSelectedGraphNode] = useState<string | undefined>();
  const [hiddenKinds, setHiddenKinds] = useState<string[]>([]);
  const [graphDepth, setGraphDepth] = useState(0); // 0 = unlimited
  const [includeAnatomy, setIncludeAnatomy] = useState(false);
  // Details card for the node selected in graph view (fetched on selection).
  const [graphSelectedConcept, setGraphSelectedConcept] = useState<Concept | null>(null);
  const [graphSelectedAnatomy, setGraphSelectedAnatomy] = useState<{ id: string; name: string; slug?: string } | null>(null);

  // Selected concept for info panel (click to view, separate from editing)
  const [selectedConcept, setSelectedConcept] = useState<Concept | null>(null);

  // Register page-search support (navbar ⌘K "Current Page" tab)
  useEffect(() => {
    setIsPageSearchSupported(true);
    return () => { setIsPageSearchSupported(false); setPageSearchTerm(''); };
  }, [setIsPageSearchSupported, setPageSearchTerm]);

  // Filter concepts by the navbar page-search term
  const filteredConcepts = useMemo(() => {
    if (!pageSearchTerm.trim()) return concepts;
    const q = pageSearchTerm.toLowerCase();
    return concepts.filter(c =>
      c.name.toLowerCase().includes(q) ||
      c.slug.toLowerCase().includes(q) ||
      c.description?.toLowerCase().includes(q) ||
      c.aliases?.some(a => a.toLowerCase().includes(q)) ||
      c.code?.toLowerCase().includes(q),
    );
  }, [concepts, pageSearchTerm]);

  const fetchConcepts = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: Parameters<typeof listConcepts>[0] = { limit: 500 };
      if (activeKind !== 'all') params.kind = activeKind;
      const data = await listConcepts(params);
      setConcepts(data);
    } catch (e) {
      setError('Failed to load concepts');
    } finally {
      setLoading(false);
    }
  }, [activeKind]);

  useEffect(() => { fetchConcepts(); }, [fetchConcepts]);

  // Fetch neighbors when a concept is selected for editing
  const fetchNeighbors = useCallback(async (conceptId: string) => {
    setNeighborsLoading(true);
    try {
      const data = await getConceptNeighbors(conceptId, { include_proposed: true });
      setNeighbors(data);
    } catch {
      setNeighbors([]);
    } finally {
      setNeighborsLoading(false);
    }
  }, []);

  // Fetch graph data (all concepts of active kind + edges between them)
  const fetchGraphData = useCallback(async () => {
    setGraphLoading(true);
    try {
      const conceptData = activeKind === 'all'
        ? await listConcepts({ limit: 1000 })
        : await listConcepts({ kind: activeKind, limit: 500 });
      const edgeData = await listEdges({ src_type: 'concept', limit: 5000 });
      const conceptIds = new Set(conceptData.map((c) => c.id));
      const conceptNodes: ConceptGraphNode[] = conceptData.map((c) => ({
        id: c.id, name: c.name, primary_kind: c.primary_kind, kinds: c.kinds, color: c.color,
      }));

      // Optionally pull in anatomy structures connected to the visible concepts
      // via polymorphic concept->anatomy edges (single batched anatomy fetch,
      // not N neighbor calls). Anatomy nodes are tagged primary_kind='anatomy'
      // so the graph renders them with the emerald Anatomy color.
      let nodes: ConceptGraphNode[] = conceptNodes;
      let edges: ConceptGraphEdgeData[] = edgeData
        .filter((e) => conceptIds.has(e.src_id) && conceptIds.has(e.dst_id))
        .map((e) => ({ id: e.id, source: e.src_id, target: e.dst_id, relation: e.relation }));

      if (includeAnatomy) {
        try {
          const anatomyRes = await anatomyService.list({ limit: 500 });
          const anatomyById = new Map(anatomyRes.items.map((a) => [a.id, a]));
          const anatomyNodeIds = new Set<string>();
          const anatomyEdges: ConceptGraphEdgeData[] = [];
          for (const e of edgeData) {
            const srcIsConcept = conceptIds.has(e.src_id);
            const dstIsConcept = conceptIds.has(e.dst_id);
            // concept -> anatomy
            if (srcIsConcept && e.dst_type === 'anatomy' && anatomyById.has(e.dst_id)) {
              anatomyNodeIds.add(e.dst_id);
              anatomyEdges.push({ id: e.id, source: e.src_id, target: e.dst_id, relation: e.relation });
            }
            // anatomy -> concept
            if (dstIsConcept && e.src_type === 'anatomy' && anatomyById.has(e.src_id)) {
              anatomyNodeIds.add(e.src_id);
              anatomyEdges.push({ id: e.id, source: e.src_id, target: e.dst_id, relation: e.relation });
            }
          }
          const anatomyNodes = [...anatomyNodeIds].map((id) => {
            const a = anatomyById.get(id)!;
            return { id, name: a.name, primary_kind: 'anatomy' as const, kinds: ['anatomy' as const], color: '#10b981' };
          });
          nodes = [...conceptNodes, ...anatomyNodes];
          edges = [...edges, ...anatomyEdges];
        } catch {
          // Anatomy fetch failed — fall back to concept-only graph.
        }
      }

      setGraphNodes(nodes);
      setGraphEdges(edges);
    } catch {
      setGraphNodes([]);
      setGraphEdges([]);
    } finally {
      setGraphLoading(false);
    }
  }, [activeKind, includeAnatomy]);

  useEffect(() => {
    if (viewMode === 'graph') fetchGraphData();
  }, [viewMode, fetchGraphData]);

  // When a graph node is selected, resolve + show its details card.
  // Concept nodes (primary_kind !== 'anatomy') → fetch the full concept;
  // anatomy nodes → show the lightweight structure info we already have.
  useEffect(() => {
    if (!selectedGraphNode) {
      setGraphSelectedConcept(null);
      setGraphSelectedAnatomy(null);
      return;
    }
    const node = graphNodes.find((n) => n.id === selectedGraphNode);
    if (!node) return;
    if (node.primary_kind === 'anatomy') {
      setGraphSelectedConcept(null);
      setGraphSelectedAnatomy({ id: node.id, name: node.name });
    } else {
      setGraphSelectedAnatomy(null);
      getConcept(selectedGraphNode)
        .then(setGraphSelectedConcept)
        .catch(() => setGraphSelectedConcept(null));
    }
  }, [selectedGraphNode, graphNodes]);

  // Depth-limited subgraph via BFS from the selected node (0 = show all)
  const displayedGraph = useMemo(() => {
    if (!selectedGraphNode || graphDepth === 0 || graphNodes.length === 0) {
      return { nodes: graphNodes, edges: graphEdges };
    }
    const adj = new Map<string, Set<string>>();
    for (const e of graphEdges) {
      if (!adj.has(e.source)) adj.set(e.source, new Set());
      if (!adj.has(e.target)) adj.set(e.target, new Set());
      adj.get(e.source)!.add(e.target);
      adj.get(e.target)!.add(e.source);
    }
    const visited = new Set<string>([selectedGraphNode]);
    let frontier = new Set([selectedGraphNode]);
    for (let d = 0; d < graphDepth; d++) {
      const next = new Set<string>();
      for (const id of frontier) {
        for (const n of adj.get(id) || []) {
          if (!visited.has(n)) { visited.add(n); next.add(n); }
        }
      }
      frontier = next;
    }
    const ids = visited;
    return {
      nodes: graphNodes.filter((n) => ids.has(n.id)),
      edges: graphEdges.filter((e) => ids.has(e.source) && ids.has(e.target)),
    };
  }, [graphNodes, graphEdges, selectedGraphNode, graphDepth]);

  // Re-fetch neighbors when editing or selection changes
  useEffect(() => {
    const id = editingId || selectedConcept?.id;
    if (id) fetchNeighbors(id);
    else if (!editingId) setNeighbors([]);
  }, [editingId, selectedConcept?.id, fetchNeighbors]);

  const handleAddEdge = async () => {
    if (!editingId) return;
    const dstId = newEdgeTargetType === 'concept' ? newEdgeTarget?.id : newEdgeAnatomy?.id;
    if (!dstId) return;
    setAddingEdge(true);
    try {
      await createEdge({
        src_type: 'concept', src_id: editingId,
        dst_type: newEdgeTargetType, dst_id: dstId,
        relation: newEdgeRelation,
      });
      setNewEdgeTarget(null);
      setNewEdgeAnatomy(null);
      await fetchNeighbors(editingId);
      if (viewMode === 'graph') fetchGraphData();
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Failed to add relationship');
    } finally {
      setAddingEdge(false);
    }
  };

  const handleDeleteEdge = async (edgeId: string) => {
    try {
      await deleteEdge(edgeId);
      if (editingId) await fetchNeighbors(editingId);
      if (viewMode === 'graph') fetchGraphData();
    } catch {
      setError('Failed to delete relationship');
    }
  };

  const generateSlug = (name: string) =>
    name.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

  const handleEdit = async (c: Concept) => {
    setEditingId(c.id);
    setIsAdding(false);
    setFormData({
      name: c.name,
      slug: c.slug,
      description: c.description || '',
      color: c.color || '#3b82f6',
      icon: (c.icon as IconConfig) || { type: 'lucide', value: 'Tag' },
      aliases: (c.aliases || []).join(', '),
      coding_system: c.coding_system || '',
      code: c.code || '',
      parent_id: c.parent_id ?? null,
      kinds: (c.kinds && c.kinds.length > 0)
        ? c.kinds
        : c.primary_kind ? [c.primary_kind] : [],
    });
    setSlugEdited(true);
    // Resolve the parent concept for the typeahead's initial display.
    setParentConcept(null);
    if (c.parent_id) {
      try {
        const p = await getConcept(c.parent_id);
        setParentConcept(p);
      } catch {
        setParentConcept(null);
      }
    }
  };

  const handleAdd = () => {
    setIsAdding(true);
    setEditingId(null);
    // Pre-select the active domain (if any) so the user doesn't have to.
    setFormData({ ...EMPTY_FORM, kinds: activeKind !== 'all' ? [activeKind] : [] });
    setSlugEdited(false);
    setParentConcept(null);
  };

  const handleOpenInGraph = (concept: Concept) => {
    setSelectedGraphNode(concept.id);
    setGraphDepth(0);
    setViewMode('graph');
  };

  const handleCancel = () => {
    setEditingId(null);
    setIsAdding(false);
    setFormData(EMPTY_FORM);
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const aliases = formData.aliases
        .split(',').map((a) => a.trim()).filter(Boolean);
      const slug = formData.slug || generateSlug(formData.name);

      if (editingId) {
        await updateConcept(editingId, {
          name: formData.name, description: formData.description || undefined,
          color: formData.color, icon: formData.icon,
          aliases, coding_system: formData.coding_system || undefined,
          code: formData.code || undefined,
          parent_id: formData.parent_id,
          kinds: formData.kinds,
        });
      } else {
        const payload: ConceptCreateInput = {
          slug, name: formData.name,
          kinds: formData.kinds,
          description: formData.description || undefined,
          color: formData.color, icon: formData.icon,
          aliases, coding_system: formData.coding_system || undefined,
          code: formData.code || undefined,
          parent_id: formData.parent_id || undefined,
        };
        await createConcept(payload);
      }
      await fetchConcepts();
      handleCancel();
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Failed to save concept');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = (c: Concept) => {
    showConfirmation({
      title: 'Delete Concept',
      message: `Delete "${c.name}"? If it has relationships it will be retired instead.`,
      confirmLabel: 'Delete',
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deleteConcept(c.id);
          await fetchConcepts();
        } catch {
          setError('Failed to delete concept');
        }
      },
    });
  };

  const handleNameChange = (name: string) => {
    setFormData((p) => ({
      ...p, name,
      slug: slugEdited ? p.slug : generateSlug(name),
    }));
  };

  const canEdit = true;

  const handleDownloadSeeds = useCallback(async () => {
    setDownloadingSeeds(true);
    try {
      await downloadSeedsZip();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to download seeds');
    } finally {
      setDownloadingSeeds(false);
    }
  }, []);

  // Searchable domain-selector options (the kind dropdown).
  const kindOptions: DropdownOption[] = useMemo(() => [
    { value: 'all', label: 'All Domains', description: 'every kind' },
    ...MANAGEABLE_KINDS.map((k) => ({
      value: k,
      label: CONCEPT_KIND_LABELS[k],
      description: k,
    })),
  ], []);

  const headerIcon = useMemo(() => <Network className="w-5 h-5" />, []);
  const headerBreadcrumbs = useMemo(() => [{ label: 'Admin', path: '/admin' }], []);

  return (
    <>
      <PageHeader
        title="Taxonomy Manager"
        icon={headerIcon}
        breadcrumbs={headerBreadcrumbs}
      />

      <div className="max-w-6xl mx-auto px-4 pb-10">
        {error && (
          <div className="mb-4 rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 px-4 py-3 text-sm text-red-700 dark:text-red-300">
            {error}
            <button onClick={() => setError(null)} className="ml-2 font-bold">✕</button>
          </div>
        )}

        <div className="mb-4 flex items-center gap-2 flex-wrap">
          <div className="w-64">
            <SearchableDropdown
              options={kindOptions}
              value={activeKind}
              onChange={(v) => { setActiveKind(v as ConceptKind | 'all'); handleCancel(); setSelectedConcept(null); }}
              searchPlaceholder="Search domains..."
            />
          </div>
          <span className="text-sm text-slate-400">
            ({pageSearchTerm ? `${filteredConcepts.length}/${concepts.length}` : concepts.length})
          </span>

          {/* View toggle */}
          <div className="flex bg-slate-100 dark:bg-slate-800 rounded-lg p-1 ml-1">
            <button
              onClick={() => setViewMode('list')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-all ${viewMode === 'list' ? 'bg-white dark:bg-slate-700 text-blue-600 shadow-sm' : 'text-slate-400 hover:text-slate-600'}`}
            >
              <List className="w-4 h-4" /> List
            </button>
            <button
              onClick={() => setViewMode('graph')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-all ${viewMode === 'graph' ? 'bg-white dark:bg-slate-700 text-blue-600 shadow-sm' : 'text-slate-400 hover:text-slate-600'}`}
            >
              <GitBranch className="w-4 h-4" /> Graph
            </button>
          </div>

          {canEdit && viewMode === 'list' && (
            <button
              onClick={handleAdd}
              className="ml-auto flex items-center gap-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 px-3 py-2 text-sm font-medium text-white transition-colors"
            >
              <Plus className="w-4 h-4" /> Add
            </button>
          )}
          {canEdit && viewMode !== 'list' && <div className="ml-auto" />}

          {canEdit && (
            <button
              onClick={handleDownloadSeeds}
              disabled={downloadingSeeds}
              title="Download the global taxonomy/anatomy/catalog as seed JSON files (for curating the shipped seeds)"
              className={`${
                canEdit && viewMode === 'list' ? '' : ''
              } flex items-center gap-1.5 rounded-lg border border-slate-300 dark:border-slate-600 hover:bg-slate-100 dark:hover:bg-slate-700 px-3 py-2 text-sm font-medium text-slate-700 dark:text-slate-200 transition-colors disabled:opacity-50 disabled:cursor-wait`}
            >
              {downloadingSeeds ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Download className="w-4 h-4" />
              )}
              Seeds
            </button>
          )}
        </div>

        {/* === GRAPH VIEW === */}
        {viewMode === 'graph' && (
          <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 overflow-hidden">
            {/* Kind filter chips */}
            <div className="flex flex-wrap gap-1.5 p-3 border-b border-slate-100 dark:border-slate-700">
              {Object.entries(CONCEPT_KIND_LABELS).map(([k, label]) => {
                const isHidden = hiddenKinds.includes(k);
                const color = KIND_COLORS[k] || '#6b7280';
                return (
                  <button
                    key={k}
                    onClick={() => setHiddenKinds(prev =>
                      prev.includes(k) ? prev.filter(x => x !== k) : [...prev, k]
                    )}
                    className={`flex items-center gap-1.5 px-2 py-1 rounded-md text-[10px] font-bold uppercase tracking-wide border transition-all ${
                      isHidden
                        ? 'opacity-30 border-slate-200 dark:border-slate-700 text-slate-400'
                        : 'border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200'
                    }`}
                    style={!isHidden ? { backgroundColor: `${color}15`, borderColor: `${color}40` } : undefined}
                  >
                    <span className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
                    {label}
                  </button>
                );
              })}
            </div>
            {/* Depth control + anatomy toggle + focus indicator */}
            <div className="flex items-center gap-3 px-3 py-2 border-b border-slate-100 dark:border-slate-700 flex-wrap">
              <span className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Depth</span>
              {[0, 1, 2, 3, 4].map((d) => (
                <button
                  key={d}
                  onClick={() => setGraphDepth(d)}
                  disabled={!selectedGraphNode && d > 0}
                  className={`px-2 py-0.5 rounded text-[10px] font-bold transition-all disabled:opacity-30 ${
                    graphDepth === d ? 'bg-blue-600 text-white' : 'bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-300'
                  }`}
                >
                  {d === 0 ? 'All' : d}
                </button>
              ))}
              <button
                onClick={() => setIncludeAnatomy((v) => !v)}
                className={`flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold border transition-all ${
                  includeAnatomy
                    ? 'bg-emerald-600 text-white border-emerald-600'
                    : 'bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-300 border-slate-200 dark:border-slate-700'
                }`}
                title="Include anatomy structures connected to these concepts via polymorphic edges"
              >
                <span className="w-2 h-2 rounded-full" style={{ backgroundColor: '#10b981' }} />
                Anatomy
              </button>
              {selectedGraphNode && graphDepth > 0 && (
                <span className="text-[10px] text-slate-400 ml-2">
                  {displayedGraph.nodes.length} nodes within {graphDepth} hop{graphDepth > 1 ? 's' : ''}
                </span>
              )}
              {selectedGraphNode && graphDepth === 0 && (
                <span className="text-[10px] text-slate-400 ml-2">
                  {graphNodes.length} total nodes
                </span>
              )}
            </div>
            <div className="h-[560px] relative">
              {graphLoading ? (
                <LoadingState variant="section" />
              ) : (
                <ConceptGraphView
                  nodes={displayedGraph.nodes}
                  edges={displayedGraph.edges}
                  selectedNodeId={selectedGraphNode}
                  hiddenKinds={hiddenKinds}
                  onSelectNode={setSelectedGraphNode}
                  onFocusNode={(id) => {
                     const c = graphNodes.find(n => n.id === id);
                     if (c) {
                       // Only switch domain if the focused node is a real concept kind
                       // (anatomy nodes have primary_kind='anatomy', not a ConceptKind).
                       const pk = c.primary_kind;
                       if (pk && pk !== 'anatomy' && (MANAGEABLE_KINDS as readonly string[]).includes(pk)) {
                         setActiveKind(pk as ConceptKind);
                       }
                       const fullConcept = concepts.find(c2 => c2.id === id);
                       if (fullConcept) handleEdit(fullConcept);
                       setViewMode('list');
                     }
                   }}
                />
              )}

              {/* Selected-node details card overlay (concept or anatomy) */}
              {selectedGraphNode && (graphSelectedConcept || graphSelectedAnatomy) && (
                <div className="absolute top-3 right-3 w-64 max-h-[520px] overflow-y-auto rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 shadow-xl p-4 space-y-3 z-10">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <div
                        className="flex items-center justify-center w-9 h-9 rounded-lg shrink-0"
                        style={{
                          backgroundColor: `${(graphSelectedConcept?.color || '#10b981')}20`,
                          color: graphSelectedConcept?.color || '#10b981',
                        }}
                      >
                        <DynamicIcon
                          icon={(graphSelectedConcept?.icon as IconConfig) || { type: 'lucide', value: 'Activity' }}
                          className="w-4 h-4"
                        />
                      </div>
                      <div className="min-w-0">
                        <div className="text-sm font-bold truncate">{graphSelectedConcept?.name || graphSelectedAnatomy?.name}</div>
                        {graphSelectedConcept ? (
                          <div className="flex flex-wrap gap-1 mt-0.5">
                            {(graphSelectedConcept.kinds || []).map((k) => (
                              <span key={k} className="px-1.5 py-0.5 rounded text-[8px] font-bold uppercase tracking-wide"
                                style={{ backgroundColor: `${KIND_COLORS[k] || '#6b7280'}15`, color: KIND_COLORS[k] || '#6b7280' }}>
                                {CONCEPT_KIND_LABELS[k] || k}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span className="px-1.5 py-0.5 rounded text-[8px] font-bold uppercase tracking-wide bg-emerald-100 dark:bg-emerald-950 text-emerald-600">Anatomy</span>
                        )}
                      </div>
                    </div>
                    <button onClick={() => setSelectedGraphNode(undefined)} className="text-slate-400 hover:text-slate-600 shrink-0">
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>

                  {graphSelectedConcept?.description && (
                    <p className="text-xs text-slate-500 dark:text-slate-400 line-clamp-3">{graphSelectedConcept.description}</p>
                  )}
                  {graphSelectedConcept && (
                    <div className="text-[10px] text-slate-400 font-mono truncate">{graphSelectedConcept.slug}</div>
                  )}

                  {graphSelectedConcept && (
                    <div className="flex items-center gap-1.5 pt-1 border-t border-slate-100 dark:border-slate-700">
                      <button
                        onClick={() => { handleEdit(graphSelectedConcept); setViewMode('list'); }}
                        className="flex-1 flex items-center justify-center gap-1 rounded-md bg-blue-600 hover:bg-blue-700 px-2 py-1.5 text-[10px] font-medium text-white"
                      >
                        <Edit2 className="w-3 h-3" /> Edit
                      </button>
                      <button
                        onClick={() => { const fc = graphSelectedConcept; if (fc) handleEdit(fc); setViewMode('list'); }}
                        className="flex-1 flex items-center justify-center gap-1 rounded-md bg-slate-100 dark:bg-slate-700 hover:bg-slate-200 dark:hover:bg-slate-600 px-2 py-1.5 text-[10px] font-medium text-slate-700 dark:text-slate-200"
                      >
                        <List className="w-3 h-3" /> In list
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* === LIST VIEW === */}
        {viewMode === 'list' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2">
            {loading ? (
              <LoadingState variant="section" />
            ) : filteredConcepts.length === 0 ? (
              <div className="rounded-lg border border-dashed border-slate-300 dark:border-slate-600 p-8 text-center text-slate-400">
                {pageSearchTerm ? `No results for "${pageSearchTerm}"` : 'No concepts yet. Click "Add" to create one.'}
              </div>
            ) : (
              <div className="space-y-2">
                {filteredConcepts.map((c) => (
                  <div
                    key={c.id}
                    onClick={() => { setSelectedConcept(c); setEditingId(null); setIsAdding(false); }}
                    className={`group flex items-center gap-3 rounded-lg border bg-white dark:bg-slate-800 px-3 py-3 transition-all cursor-pointer hover:shadow-sm ${
                      selectedConcept?.id === c.id
                        ? 'border-blue-400 dark:border-blue-500 ring-1 ring-blue-200 dark:ring-blue-800'
                        : 'border-slate-200 dark:border-slate-700 hover:border-slate-300 dark:hover:border-slate-600'
                    }`}
                  >
                    <div
                      className="flex items-center justify-center w-9 h-9 rounded-lg shrink-0"
                      style={{ backgroundColor: `${c.color || '#3b82f6'}20`, color: c.color || '#3b82f6' }}
                    >
                      <DynamicIcon icon={(c.icon as IconConfig) || { type: 'lucide', value: 'Tag' }} className="w-4 h-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium truncate flex items-center gap-2">
                        {c.name}
                        {c.status === 'retired' && <span className="text-[9px] uppercase font-bold text-amber-500">retired</span>}
                      </div>
                      <div className="text-xs text-slate-400 truncate flex items-center gap-1.5">
                        <span>{c.slug}</span>
                        {c.code && <span className="font-mono">· {c.coding_system}:{c.code}</span>}
                        {activeKind === 'all' && (c.kinds || []).map((k) => (
                          <span key={k} className="px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wide"
                            style={{ backgroundColor: `${KIND_COLORS[k] || '#6b7280'}15`, color: KIND_COLORS[k] || '#6b7280' }}>
                            {CONCEPT_KIND_LABELS[k]}
                          </span>
                        ))}
                      </div>
                    </div>
                    {canEdit && (
                      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity" onClick={(e) => e.stopPropagation()}>
                        <button onClick={() => handleEdit(c)} className="p-1.5 rounded hover:bg-slate-100 dark:hover:bg-slate-700">
                          <Edit2 className="w-3.5 h-3.5" />
                        </button>
                        <button onClick={() => handleDelete(c)} className="p-1.5 rounded hover:bg-red-50 dark:hover:bg-red-950/30 text-red-500">
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Info panel (click-to-view) */}
          {selectedConcept && !editingId && !isAdding && (
            <div className="lg:col-span-1">
              <div className="sticky top-8 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5 space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200">Concept Details</h3>
                  <button onClick={() => setSelectedConcept(null)} className="text-slate-400 hover:text-slate-600">
                    <X className="w-4 h-4" />
                  </button>
                </div>

                <div className="flex items-center gap-3">
                  <div
                    className="flex items-center justify-center w-12 h-12 rounded-xl shrink-0"
                    style={{ backgroundColor: `${selectedConcept.color || '#3b82f6'}20`, color: selectedConcept.color || '#3b82f6' }}
                  >
                    <DynamicIcon icon={(selectedConcept.icon as IconConfig) || { type: 'lucide', value: 'Tag' }} className="w-6 h-6" />
                  </div>
                  <div className="min-w-0">
                    <div className="text-base font-bold truncate">{selectedConcept.name}</div>
                    <div className="flex flex-wrap gap-1 mt-1">
                      {(selectedConcept.kinds || []).map((k) => (
                        <span key={k} className="px-2 py-0.5 rounded text-[9px] font-bold uppercase tracking-wide"
                          style={{ backgroundColor: `${KIND_COLORS[k] || '#6b7280'}15`, color: KIND_COLORS[k] || '#6b7280' }}>
                          {CONCEPT_KIND_LABELS[k]}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>

                {selectedConcept.description && (
                  <p className="text-sm text-slate-500 dark:text-slate-400">{selectedConcept.description}</p>
                )}

                <div className="space-y-2 text-xs">
                  <div className="flex justify-between">
                    <span className="text-slate-400 font-medium">Slug</span>
                    <span className="font-mono text-slate-600 dark:text-slate-300">{selectedConcept.slug}</span>
                  </div>
                  {selectedConcept.coding_system && (
                    <div className="flex justify-between">
                      <span className="text-slate-400 font-medium">Coding</span>
                      <span className="font-mono text-slate-600 dark:text-slate-300">{selectedConcept.coding_system}:{selectedConcept.code || '?'}</span>
                    </div>
                  )}
                  <div className="flex justify-between">
                    <span className="text-slate-400 font-medium">Status</span>
                    <span className={`font-bold uppercase ${selectedConcept.status === 'active' ? 'text-green-500' : 'text-amber-500'}`}>{selectedConcept.status}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400 font-medium">Relationships</span>
                    <span className="font-bold text-slate-600 dark:text-slate-300">{neighbors.length}</span>
                  </div>
                </div>

                {selectedConcept.aliases && selectedConcept.aliases.length > 0 && (
                  <div>
                    <span className="text-xs text-slate-400 font-medium block mb-2">Aliases</span>
                    <div className="flex flex-wrap gap-1.5">
                      {selectedConcept.aliases.map((a, i) => (
                        <span key={i} className="px-2 py-0.5 bg-slate-100 dark:bg-slate-700 rounded text-[10px] font-medium text-slate-500 dark:text-slate-300">{a}</span>
                      ))}
                    </div>
                  </div>
                )}

                {neighbors.length > 0 && (
                  <div>
                    <span className="text-xs text-slate-400 font-medium block mb-2">Linked to</span>
                    <div className="space-y-1 max-h-40 overflow-y-auto">
                      {neighbors.slice(0, 10).map((n) => (
                        <div key={n.edge.id} className="flex items-center gap-1.5 text-xs">
                          <span className="text-[9px] font-bold uppercase text-slate-400 w-16 shrink-0">{n.edge.relation.replace(/_/g, ' ')}</span>
                          <span className="text-slate-400">{n.direction === 'outgoing' ? '→' : '←'}</span>
                            <span className="truncate font-medium text-slate-600 dark:text-slate-300">
                              {n.endpoint?.label || `${n.edge.dst_type}:${n.edge.dst_id.slice(0, 8)}`}
                            </span>
                        </div>
                      ))}
                      {neighbors.length > 10 && <div className="text-[10px] text-slate-400 italic">+{neighbors.length - 10} more</div>}
                    </div>
                  </div>
                )}

                <div className="flex items-center gap-2">
                  <button
                    onClick={() => handleEdit(selectedConcept)}
                    className="flex-1 flex items-center justify-center gap-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 px-3 py-2 text-xs font-medium text-white transition-colors"
                  >
                    <Edit2 className="w-3.5 h-3.5" /> Edit
                  </button>
                  <button
                    onClick={() => handleOpenInGraph(selectedConcept)}
                    className="flex-1 flex items-center justify-center gap-1.5 rounded-lg bg-slate-100 dark:bg-slate-700 hover:bg-slate-200 dark:hover:bg-slate-600 px-3 py-2 text-xs font-medium text-slate-700 dark:text-slate-200 transition-colors"
                  >
                    <Maximize2 className="w-3.5 h-3.5" /> Open in Graph
                  </button>
                </div>
              </div>
            </div>
          )}

          {(editingId || isAdding) && (
            <div className="lg:col-span-1">
              <div className="sticky top-8 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5 space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold">
                    {editingId ? 'Edit Concept' : 'New Concept'}
                  </h3>
                  <button onClick={handleCancel} className="text-slate-400 hover:text-slate-600">
                    <X className="w-4 h-4" />
                  </button>
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1.5">
                    Domains <span className="text-red-400">*</span>
                    <span className="ml-1 text-[10px] font-normal text-slate-400">(a concept can belong to several)</span>
                  </label>
                  <div className="flex flex-wrap gap-1.5">
                    {MANAGEABLE_KINDS.map((k) => {
                      const selected = formData.kinds.includes(k);
                      const color = KIND_COLORS[k] || '#6b7280';
                      return (
                        <button
                          key={k}
                          type="button"
                          onClick={() => setFormData((p) => ({
                            ...p,
                            kinds: selected ? p.kinds.filter((x) => x !== k) : [...p.kinds, k],
                          }))}
                          className={`px-2 py-1 rounded-md text-[10px] font-bold uppercase tracking-wide border transition-all ${
                            selected
                              ? 'text-slate-700 dark:text-slate-200'
                              : 'opacity-50 hover:opacity-100 border-slate-200 dark:border-slate-700 text-slate-400'
                          }`}
                          style={selected ? { backgroundColor: `${color}15`, borderColor: `${color}40` } : undefined}
                        >
                          <span className="inline-block w-2 h-2 rounded-full mr-1 align-middle" style={{ backgroundColor: color }} />
                          {CONCEPT_KIND_LABELS[k]}
                        </button>
                      );
                    })}
                  </div>
                  {formData.kinds.length === 0 && (
                    <p className="mt-1 text-[10px] text-amber-500">Select at least one domain.</p>
                  )}
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">Name</label>
                  <input
                    value={formData.name}
                    onChange={(e) => handleNameChange(e.target.value)}
                    placeholder="e.g. Cardiology"
                    className="w-full rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">Slug</label>
                  <input
                    value={formData.slug}
                    onChange={(e) => { setFormData((p) => ({ ...p, slug: e.target.value })); setSlugEdited(true); }}
                    placeholder="auto-generated"
                    disabled={!!editingId}
                    className="w-full rounded-lg border border-slate-300 dark:border-slate-600 bg-slate-50 dark:bg-slate-900/50 px-3 py-2 text-sm outline-none disabled:opacity-50"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">Description</label>
                  <textarea
                    value={formData.description}
                    onChange={(e) => setFormData((p) => ({ ...p, description: e.target.value }))}
                    rows={2}
                    className="w-full rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-slate-500 mb-1">Coding System</label>
                    <input
                      value={formData.coding_system}
                      onChange={(e) => setFormData((p) => ({ ...p, coding_system: e.target.value }))}
                      placeholder="snomed, loinc, atc..."
                      className="w-full rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-500 mb-1">Code</label>
                    <input
                      value={formData.code}
                      onChange={(e) => setFormData((p) => ({ ...p, code: e.target.value }))}
                      placeholder="e.g. 394579002"
                      className="w-full rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">Aliases (comma-separated)</label>
                  <input
                    value={formData.aliases}
                    onChange={(e) => setFormData((p) => ({ ...p, aliases: e.target.value }))}
                    placeholder="cardiac, heart medicine"
                    className="w-full rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">Color</label>
                  <div className="flex flex-wrap gap-1.5">
                    {COMMON_COLORS.map((color) => (
                      <button
                        key={color}
                        onClick={() => setFormData((p) => ({ ...p, color }))}
                        className={`w-6 h-6 rounded-full border-2 transition-transform ${formData.color === color ? 'border-slate-900 dark:border-white scale-110' : 'border-transparent'}`}
                        style={{ backgroundColor: color }}
                      />
                    ))}
                    <input
                      type="color"
                      value={formData.color}
                      onChange={(e) => setFormData((p) => ({ ...p, color: e.target.value }))}
                      className="w-6 h-6 rounded cursor-pointer"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">Icon</label>
                  <IconPicker
                    value={formData.icon}
                    color={formData.color}
                    onChange={(icon) => setFormData((p) => ({ ...p, icon }))}
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">Parent concept</label>
                  <TaxonomyTypeahead
                    initialConcept={parentConcept}
                    onSelect={(c) => setFormData((p) => ({ ...p, parent_id: c?.id ?? null }))}
                    placeholder="Search parent concept..."
                  />
                </div>

                <button
                  onClick={handleSave}
                  disabled={saving || !formData.name || formData.kinds.length === 0}
                  className="w-full flex items-center justify-center gap-2 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:opacity-50 px-4 py-2 text-sm font-medium text-white transition-colors"
                >
                  {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                  {editingId ? 'Update' : 'Create'}
                </button>

                {/* === RELATIONSHIPS PANEL === */}
                {editingId && (
                  <div className="mt-6 pt-4 border-t border-slate-200 dark:border-slate-600">
                    <div className="flex items-center gap-2 mb-3">
                      <Link2 className="w-4 h-4 text-slate-400" />
                      <h4 className="text-xs font-bold text-slate-500 uppercase tracking-widest">
                        Relationships
                      </h4>
                    </div>

                    {neighborsLoading ? (
                      <Loader2 className="w-4 h-4 animate-spin text-slate-400" />
                    ) : neighbors.length === 0 ? (
                      <p className="text-xs text-slate-400 italic mb-3">No relationships yet.</p>
                    ) : (
                      <div className="space-y-1.5 mb-3">
                        {neighbors.map((n) => (
                          <div key={n.edge.id} className="group flex items-center gap-2 rounded-md border border-slate-200 dark:border-slate-600 px-2 py-1.5 text-xs">
                            <span className="font-bold text-slate-500 uppercase text-[9px] tracking-wide w-16 shrink-0">
                              {n.edge.relation.replace(/_/g, ' ')}
                            </span>
                            <span className="text-slate-400 text-[9px]">{n.direction === 'outgoing' ? '→' : '←'}</span>
                            <span
                              className="flex items-center gap-1 flex-1 truncate font-medium"
                              style={{ color: n.endpoint?.color || undefined }}
                            >
                              {n.endpoint?.icon && (
                                <DynamicIcon icon={(n.endpoint.icon as IconConfig)!} className="w-3 h-3 shrink-0" color={n.endpoint?.color || undefined} />
                              )}
                              {n.endpoint?.label || `${n.edge.dst_type}:${n.edge.dst_id.slice(0, 8)}`}
                              {n.endpoint && n.endpoint.type !== 'concept' && (
                                <span className="ml-1 px-1 rounded bg-slate-100 dark:bg-slate-700 text-[8px] uppercase text-slate-400">{n.endpoint.type}</span>
                              )}
                            </span>
                            <button
                              onClick={() => handleDeleteEdge(n.edge.id)}
                              className="opacity-0 group-hover:opacity-100 p-1 text-red-400 hover:text-red-600 transition-opacity"
                            >
                              <Unlink className="w-3 h-3" />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Add relationship form */}
                    <div className="space-y-2">
                      {/* Target-type toggle: link to a concept or an anatomy structure */}
                      <div className="flex bg-slate-100 dark:bg-slate-900 rounded-lg p-0.5">
                        {(['concept', 'anatomy'] as const).map((t) => (
                          <button
                            key={t}
                            type="button"
                            onClick={() => { setNewEdgeTargetType(t); setNewEdgeTarget(null); setNewEdgeAnatomy(null); }}
                            className={`flex-1 px-2 py-1 rounded-md text-[10px] font-bold uppercase tracking-wide transition-all ${newEdgeTargetType === t ? 'bg-white dark:bg-slate-700 text-blue-600 shadow-sm' : 'text-slate-400'}`}
                          >
                            {t}
                          </button>
                        ))}
                      </div>

                      {newEdgeTargetType === 'concept' ? (
                        <TaxonomyTypeahead
                          onSelect={(c) => setNewEdgeTarget(c)}
                          value={null}
                          placeholder="Link to concept…"
                        />
                      ) : (
                        <AnatomyTypeahead
                          onSelect={(s) => setNewEdgeAnatomy(s)}
                          value={null}
                          placeholder="Link to anatomy (organ / structure)…"
                        />
                      )}
                  <select
                    value={newEdgeRelation}
                    onChange={(e) => setNewEdgeRelation(e.target.value)}
                    className="w-full rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-xs outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    {['EXAMINES', 'IMAGES', 'PERFORMS', 'ORDERS', 'MEMBER_OF', 'PART_OF', 'TREATS', 'PREVENTS', 'INDICATES', 'MONITORS', 'RISK_OF', 'SCREENS_FOR', 'CORRELATES_WITH', 'CONTRAINDICATES', 'CAUSED_BY', 'LOCATED_IN', 'CLASSIFIED_AS'].map(r => (
                      <option key={r} value={r}>{r.replace(/_/g, ' ')}</option>
                    ))}
                  </select>
                  <button
                    onClick={handleAddEdge}
                    disabled={addingEdge || (newEdgeTargetType === 'concept' ? !newEdgeTarget : !newEdgeAnatomy)}
                    className="w-full flex items-center justify-center gap-1.5 rounded-lg bg-slate-600 hover:bg-slate-700 disabled:opacity-30 px-3 py-1.5 text-xs font-medium text-white transition-colors"
                  >
                    {addingEdge ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
                    Add Link
                  </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
        )}
      </div>
    </>
  );
}
