import React, { useState, useCallback, useEffect, useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Activity, Network, Plus, Crosshair, Layers, X } from 'lucide-react';
import { anatomyService } from '../../services/anatomyService';
import type {
  AnatomyStructure,
  AnatomyRelatedResponse,
  AnatomyCategory,
} from '../../types/anatomy';
import { CATEGORY_COLORS } from '../../types/anatomy';
import { useAuthStore } from '../../store/slices/authSlice';
import { PageHeader } from '../../components/ui/PageHeader';
import { PageContainer } from '../../components/ui/PageContainer';
import { LoadingState } from '../../components/ui/LoadingState';
import { BodyMapSVG, type BodyMapMarkerSpec } from '../../components/anatomy/BodyMapSVG';
import { AnatomySearchPopup } from '../../components/anatomy/AnatomySearchPopup';
import { AnatomyDetail } from '../../components/anatomy/AnatomyDetail';
import { AnatomyGraphModal } from '../../components/anatomy/AnatomyGraphModal';
import { AnatomyStructureForm } from '../../components/anatomy/AnatomyStructureForm';
import { PositionEditor } from '../../components/anatomy/PositionEditor';
import { getMarker, figuresByGroup, useAnatomyAtlas } from '../../components/anatomy/atlas';

const CATEGORY_FILTERS: AnatomyCategory[] = [
  'SYSTEM',
  'REGION',
  'ORGAN',
  'ORGAN_PART',
  'TISSUE',
  'CELL',
  'JOINT',
];

export const AnatomyExplorer: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { slug: routeSlug } = useParams<{ slug: string }>();

  const [selected, setSelected] = useState<AnatomyStructure | null>(null);
  const [related, setRelated] = useState<AnatomyRelatedResponse | null>(null);
  const [isLoadingStructure, setIsLoadingStructure] = useState(false);
  const [isLoadingOverview, setIsLoadingOverview] = useState(false);
  const [systems, setSystems] = useState<AnatomyStructure[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [activeCategories, setActiveCategories] = useState<Set<AnatomyCategory>>(new Set());
  const [isGraphOpen, setIsGraphOpen] = useState(false);
  const [isFormOpen, setIsFormOpen] = useState(false);
  const [isEditorOpen, setIsEditorOpen] = useState(false);
  const [figureSlug, setFigureSlug] = useState<string>('man-front');
  const [notFoundSlug, setNotFoundSlug] = useState<string | null>(null);

  const isAdmin = useAuthStore((s) => s.user?.role === 'SYSTEM_ADMIN');
  const figures = useAnatomyAtlas((s) => s.figures);
  const figureOrder = useAnatomyAtlas((s) => s.figureOrder);
  const ensureLoaded = useAnatomyAtlas((s) => s.ensureLoaded);

  useEffect(() => {
    ensureLoaded();
  }, [ensureLoaded]);

  useEffect(() => {
    if (figureOrder.length && !figureOrder.includes(figureSlug)) {
      setFigureSlug(figureOrder[0]);
    }
  }, [figureOrder, figureSlug]);

  const figureGroups = useMemo(() => figuresByGroup(figures), [figures]);

  // ---- Landing overview: systems + total count (only when nothing selected) ----
  useEffect(() => {
    if (selected || systems.length > 0) return;
    let mounted = true;
    setIsLoadingOverview(true);
    Promise.all([
      anatomyService.list({ category: 'SYSTEM', limit: 100 }),
      anatomyService.list({ limit: 1 }),
    ])
      .then(([sysRes, anyRes]) => {
        if (!mounted) return;
        setSystems(sysRes.items);
        setTotalCount(anyRes.total);
      })
      .catch((err) => console.error('Failed to load anatomy overview', err))
      .finally(() => mounted && setIsLoadingOverview(false));
    return () => {
      mounted = false;
    };
  }, [selected, systems.length]);

  // ---- Deep-linking: sync URL <-> selection ----
  const loadStructure = useCallback(
    async (structure: AnatomyStructure, replace = false) => {
      setSelected(structure);
      setRelated(null);
      setNotFoundSlug(null);
      if (structure.slug !== routeSlug) {
        const path = `/anatomy/${structure.slug}`;
        if (replace) navigate(path, { replace: true });
        else navigate(path);
      }
      setIsLoadingStructure(true);
      try {
        const data = await anatomyService.getRelated(structure.slug);
        setRelated(data);
      } catch (err) {
        console.error('Failed to load related structures', err);
      } finally {
        setIsLoadingStructure(false);
      }
    },
    [navigate, routeSlug]
  );

  // Load from URL on mount / when route changes
  useEffect(() => {
    if (!routeSlug) {
      setSelected(null);
      setRelated(null);
      return;
    }
    if (selected?.slug === routeSlug) return;
    let mounted = true;
    setIsLoadingStructure(true);
    anatomyService
      .get(routeSlug)
      .then((structure) => {
        if (mounted) return loadStructure(structure, true);
      })
      .catch(() => {
        if (mounted) {
          setNotFoundSlug(routeSlug);
          setIsLoadingStructure(false);
        }
      });
    return () => {
      mounted = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeSlug]);

  const handleBodyMapSelect = useCallback(
    async (slug: string) => {
      try {
        const data = await anatomyService.get(slug);
        await loadStructure(data);
      } catch (err) {
        console.error('Failed to load structure by slug', err);
      }
    },
    [loadStructure]
  );

  const handleSelectRelated = useCallback(
    (structure: AnatomyStructure) => {
      loadStructure(structure);
    },
    [loadStructure]
  );

  const toggleCategory = (cat: AnatomyCategory) => {
    setActiveCategories((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  };

  // Build marker overlays for the body map: selected organ + related organs
  const bodyMapMarkers: BodyMapMarkerSpec[] = useMemo(() => {
    const specs: BodyMapMarkerSpec[] = [];
    const selMarker = selected ? getMarker(selected, figureSlug) : null;
    if (selected && selMarker) {
      specs.push({
        marker: selMarker,
        slug: selected.slug,
        label: selected.name,
        variant: 'selected',
      });
    }
    const relatedList = [...(related?.incoming ?? []), ...(related?.outgoing ?? [])];
    for (const r of relatedList) {
      const m = getMarker(r.structure, figureSlug);
      if (m && r.structure.slug !== selected?.slug) {
        specs.push({
          marker: m,
          slug: r.structure.slug,
          label: r.structure.name,
          variant: 'highlight',
        });
      }
    }
    return specs;
  }, [selected, related, figureSlug]);

  const activeCategoryArray = useMemo(
    () => (activeCategories.size > 0 ? Array.from(activeCategories) : undefined),
    [activeCategories]
  );

  return (
    <>
      <PageHeader
        title={t('anatomy.title')}
        subtitle={t('anatomy.subtitle')}
        icon={<Network className="w-6 h-6 text-blue-500" />}
      />

      <PageContainer className="!space-y-0 px-6 pt-2 pb-6">
        {/* Toolbar: search + add custom */}
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex-1 min-w-[240px] max-w-md">
            <AnatomySearchPopup
              selectedId={selected?.id}
              onSelect={handleSelectRelated}
              categoryFilter={activeCategoryArray}
            />
          </div>
          <button
            onClick={() => setIsFormOpen(true)}
            className="flex items-center gap-1.5 px-3 py-2 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-xl text-xs font-black uppercase tracking-widest hover:bg-blue-100 dark:hover:bg-blue-900/40 transition-colors"
          >
            <Plus className="w-3.5 h-3.5" />
            {t('anatomy.add_custom')}
          </button>
          {isAdmin && (
            <button
              onClick={() => setIsEditorOpen(true)}
              title={t('anatomy.editor_title', { defaultValue: 'Position Editor' })}
              className="flex items-center gap-1.5 px-3 py-2 bg-gray-100 dark:bg-dark-bg text-gray-600 dark:text-dark-muted rounded-xl text-xs font-black uppercase tracking-widest hover:bg-gray-200 dark:hover:bg-dark-border transition-colors"
            >
              <Crosshair className="w-3.5 h-3.5" />
              {t('anatomy.editor_button', { defaultValue: 'Positions' })}
            </button>
          )}
          {isAdmin && (
            <button
              onClick={() => navigate('/admin/anatomy-atlas')}
              title={t('anatomy.atlas_manager', { defaultValue: 'Atlas Manager' })}
              className="flex items-center gap-1.5 px-3 py-2 bg-gray-100 dark:bg-dark-bg text-gray-600 dark:text-dark-muted rounded-xl text-xs font-black uppercase tracking-widest hover:bg-gray-200 dark:hover:bg-dark-border transition-colors"
            >
              <Layers className="w-3.5 h-3.5" />
              {t('anatomy.atlas_manager', { defaultValue: 'Atlas' })}
            </button>
          )}
        </div>

        {/* Category filters */}
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[10px] font-black uppercase text-gray-400 tracking-widest mr-1">
            {t('anatomy.filters')}:
          </span>
          {CATEGORY_FILTERS.map((cat) => {
            const isActive = activeCategories.has(cat);
            return (
              <button
                key={cat}
                onClick={() => toggleCategory(cat)}
                className={`px-2.5 py-1 text-[10px] font-bold rounded-full transition-all ${
                  isActive
                    ? 'text-white'
                    : 'bg-gray-100 dark:bg-dark-bg text-gray-400 hover:bg-gray-200 dark:hover:bg-dark-border'
                }`}
                style={isActive ? { background: CATEGORY_COLORS[cat] } : {}}
              >
                {t(`anatomy.categories.${cat}`)}
              </button>
            );
          })}
          {activeCategories.size > 0 && (
            <button
              onClick={() => setActiveCategories(new Set())}
              className="flex items-center gap-1 text-[10px] text-gray-400 hover:text-red-500 ml-1"
            >
              <X className="w-3 h-3" />
              {t('anatomy.clear')}
            </button>
          )}
        </div>

        {/* Main workspace: body map + detail */}
        <div className="flex-1 flex gap-4 min-h-0">
          {/* Left: body map */}
          <div className="w-[240px] flex-shrink-0 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-2xl p-4 flex flex-col items-center">
            <div className="flex items-center justify-between w-full mb-3 gap-2">
              <p className="text-[10px] font-black uppercase text-gray-400 tracking-widest">
                {t('anatomy.body_map')}
              </p>
              <select
                value={figureSlug}
                onChange={(e) => setFigureSlug(e.target.value)}
                className="text-[10px] font-black uppercase bg-gray-100 dark:bg-dark-bg text-gray-600 dark:text-dark-muted rounded-lg px-2 py-1 outline-none cursor-pointer"
              >
                {Object.entries(figureGroups).map(([groupKey, figs]) => (
                  <optgroup key={groupKey} label={groupKey}>
                    {figs.map((f) => (
                      <option key={f.slug} value={f.slug}>
                        {f.label}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </div>
            <BodyMapSVG
              figureSlug={figureSlug}
              selectedSlug={selected?.slug}
              markers={bodyMapMarkers}
              onSelect={handleBodyMapSelect}
            />
          </div>

          {/* Middle: detail or landing overview */}
          <div className="flex-1 min-w-0 max-w-4xl overflow-y-auto custom-scrollbar pr-1">
            {selected ? (
              <AnatomyDetail
                key={selected.id}
                structure={selected}
                related={related}
                onSelectRelated={handleSelectRelated}
                onViewGraph={() => setIsGraphOpen(true)}
              />
            ) : notFoundSlug ? (
              <div className="h-full flex flex-col items-center justify-center text-center">
                <Activity className="w-16 h-16 text-gray-200 dark:text-dark-border mb-4" />
                <p className="text-lg font-bold text-gray-400">
                  {t('anatomy.no_selection_title')}
                </p>
                <p className="text-sm text-gray-300 mt-1">“{notFoundSlug}”</p>
              </div>
            ) : (
              <LandingOverview
                systems={systems}
                totalCount={totalCount}
                isLoading={isLoadingOverview}
                onSelect={handleSelectRelated}
              />
            )}
            {isLoadingStructure && (
              <div className="mt-4">
                <LoadingState variant="mini" showText={false} />
              </div>
            )}
          </div>
        </div>
      </PageContainer>

      {/* Relationship graph modal */}
      {selected && (
        <AnatomyGraphModal
          isOpen={isGraphOpen}
          onClose={() => setIsGraphOpen(false)}
          initialStructure={selected}
          onNavigate={(s) => {
            handleSelectRelated(s);
            setIsGraphOpen(false);
          }}
        />
      )}

      {/* Create / edit custom structure */}
      <AnatomyStructureForm
        isOpen={isFormOpen}
        onClose={() => setIsFormOpen(false)}
        onSaved={(s) => {
          loadStructure(s);
        }}
      />

      {/* Admin: drag-to-place organ markers */}
      <PositionEditor isOpen={isEditorOpen} onClose={() => setIsEditorOpen(false)} />
    </>
  );
};

// ---------------------------------------------------------------------------
// Landing overview: shown when no structure is selected
// ---------------------------------------------------------------------------

interface LandingOverviewProps {
  systems: AnatomyStructure[];
  totalCount: number;
  isLoading: boolean;
  onSelect: (s: AnatomyStructure) => void;
}

const LandingOverview: React.FC<LandingOverviewProps> = ({
  systems,
  totalCount,
  isLoading,
  onSelect,
}) => {
  const { t } = useTranslation();

  if (isLoading) {
    return <LoadingState variant="section" message={t('anatomy.loading')} />;
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="bg-gradient-to-br from-blue-500 to-blue-600 rounded-2xl p-6 text-white">
        <div className="flex items-center gap-2 mb-1">
          <Layers className="w-5 h-5" />
          <h2 className="text-lg font-black">{t('anatomy.overview_title')}</h2>
        </div>
        <p className="text-sm text-blue-100">
          {t('anatomy.overview_subtitle', { count: totalCount })}
        </p>
      </div>

      <div>
        <p className="text-[10px] font-black uppercase text-gray-400 tracking-widest mb-3">
          {t('anatomy.systems')}
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {systems.map((sys) => (
            <button
              key={sys.id}
              onClick={() => onSelect(sys)}
              className="flex items-center justify-between px-4 py-3 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-xl text-sm hover:border-blue-300 dark:hover:border-blue-700 transition-colors group text-left"
            >
              <div className="flex items-center gap-2 min-w-0">
                <span
                  className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                  style={{ background: CATEGORY_COLORS.SYSTEM }}
                />
                <span className="font-medium text-gray-700 dark:text-dark-text truncate">
                  {sys.name}
                </span>
              </div>
              <span className="text-[9px] text-gray-400 uppercase tracking-tight flex-shrink-0">
                {t(`anatomy.categories.${sys.category}`)}
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};
