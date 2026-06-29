import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal } from '../ui/Modal';
import { Search, Crosshair, Check, Loader2, ZoomIn, ZoomOut } from 'lucide-react';
import { anatomyService } from '../../services/anatomyService';
import type { AnatomyStructure, AnatomyMapMarker, MarkerMap } from '../../types/anatomy';
import { CATEGORY_COLORS } from '../../types/anatomy';
import { BodySilhouette, markerToCanvas, pointerToFigureNormalized } from './BodySilhouette';
import { useAnatomyAtlas } from './atlas';

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

/**
 * Admin tool for placing organ markers on body figures by dragging.
 *
 * Markers are stored per-figure (keyed by figure slug in
 * `display.map.markers`) as normalized 0–1 coordinates within that figure's
 * viewBox, so they stay correct at any render size and each figure is
 * positioned independently. Switch figure with the picker in the header.
 */
export const PositionEditor: React.FC<Props> = ({ isOpen, onClose }) => {
  const { t } = useTranslation();
  const figures = useAnatomyAtlas((s) => s.figures);
  const figureOrder = useAnatomyAtlas((s) => s.figureOrder);
  const ensureLoaded = useAnatomyAtlas((s) => s.ensureLoaded);
  const [figureSlug, setFigureSlug] = useState<string>('man-front');
  const [structures, setStructures] = useState<AnatomyStructure[]>([]);
  const [edits, setEdits] = useState<Record<string, AnatomyMapMarker>>({});
  const [saving, setSaving] = useState<Set<string>>(new Set());
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [zoom, setZoom] = useState(1);
  const imgWrapRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef<string | null>(null);

  useEffect(() => {
    if (isOpen) ensureLoaded();
  }, [isOpen, ensureLoaded]);

  useEffect(() => {
    if (isOpen && figureOrder.length && !figureOrder.includes(figureSlug)) {
      setFigureSlug(figureOrder[0]);
    }
  }, [isOpen, figureOrder, figureSlug]);

  useEffect(() => {
    if (!isOpen) return;
    let mounted = true;
    setIsLoading(true);
    anatomyService
      .list({ limit: 1000 })
      .then((res) => {
        if (!mounted) return;
        setStructures(res.items);
        if (!selectedSlug && res.items[0]) setSelectedSlug(res.items[0].slug);
      })
      .finally(() => mounted && setIsLoading(false));
    return () => {
      mounted = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  // Clear unsaved edits when switching figure (each figure is edited independently)
  useEffect(() => {
    setEdits({});
  }, [figureSlug]);

  const figure = figures[figureSlug];

  const markerFor = useCallback(
    (slug: string): AnatomyMapMarker | null => {
      if (edits[slug]) return edits[slug];
      const s = structures.find((x) => x.slug === slug);
      return s?.display?.map?.markers?.[figureSlug] ?? null;
    },
    [edits, structures, figureSlug]
  );

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    const list = term
      ? structures.filter((s) => s.name.toLowerCase().includes(term) || s.slug.toLowerCase().includes(term))
      : structures;
    return [...list].sort((a, b) => {
      const am = markerFor(a.slug) ? 0 : 1;
      const bm = markerFor(b.slug) ? 0 : 1;
      if (am !== bm) return am - bm;
      return a.name.localeCompare(b.name);
    });
  }, [structures, search, markerFor]);

  const handlePointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (!draggingRef.current || !figure) return;
      const rect = imgWrapRef.current?.getBoundingClientRect();
      if (!rect) return;
      const pos = pointerToFigureNormalized(figure, rect, e.clientX, e.clientY);
      if (!pos) return;
      const slug = draggingRef.current;
      setEdits((prev) => {
        const cur = prev[slug] ?? markerFor(slug) ?? { nx: 0.5, ny: 0.3, nr: 0.02 };
        return { ...prev, [slug]: { ...cur, nx: pos.nx, ny: pos.ny } };
      });
    },
    [figure, markerFor]
  );

  const saveMarker = useCallback(
    async (slug: string) => {
      const edited = edits[slug];
      if (!edited) return;
      const s = structures.find((x) => x.slug === slug);
      const base: MarkerMap = s?.display?.map?.markers ?? {};
      const next: MarkerMap = { ...base, [figureSlug]: edited };
      setSaving((p) => new Set(p).add(slug));
      try {
        await anatomyService.update(slug, { display: { map: { markers: next } } });
        setStructures((prev) =>
          prev.map((x) =>
            x.slug === slug ? { ...x, display: { map: { markers: next } } } : x
          )
        );
        setEdits((prev) => {
          const cp = { ...prev };
          delete cp[slug];
          return cp;
        });
      } catch (err) {
        console.error('Failed to save marker for', slug, err);
      } finally {
        setSaving((p) => {
          const cp = new Set(p);
          cp.delete(slug);
          return cp;
        });
      }
    },
    [edits, structures, figureSlug]
  );

  const handlePointerUp = useCallback(() => {
    const slug = draggingRef.current;
    draggingRef.current = null;
    if (slug && edits[slug]) saveMarker(slug);
  }, [edits, saveMarker]);

  const removeMarker = useCallback(
    async (slug: string) => {
      const s = structures.find((x) => x.slug === slug);
      const base: MarkerMap = s?.display?.map?.markers ?? {};
      const next: MarkerMap = { ...base };
      delete next[figureSlug];
      setSaving((p) => new Set(p).add(slug));
      try {
        await anatomyService.update(slug, { display: { map: { markers: next } } });
        setStructures((prev) =>
          prev.map((x) => (x.slug === slug ? { ...x, display: { map: { markers: next } } } : x))
        );
        setEdits((prev) => {
          const cp = { ...prev };
          delete cp[slug];
          return cp;
        });
      } catch (err) {
        console.error(err);
      } finally {
        setSaving((p) => {
          const cp = new Set(p);
          cp.delete(slug);
          return cp;
        });
      }
    },
    [structures, figureSlug]
  );

  const toggleMarker = (slug: string) => {
    if (markerFor(slug)) {
      removeMarker(slug);
    } else {
      setEdits((prev) => ({ ...prev, [slug]: { nx: 0.5, ny: 0.3, nr: 0.02 } }));
      setSelectedSlug(slug);
    }
  };

  const setMarkerSize = useCallback(
    (slug: string, nr: number) => {
      setEdits((prev) => {
        const cur = prev[slug] ?? markerFor(slug);
        if (!cur) return prev;
        return { ...prev, [slug]: { ...cur, nr } };
      });
    },
    [markerFor]
  );

  const renderedMarkers = structures
    .map((s) => ({ s, m: markerFor(s.slug) }))
    .filter((x): x is { s: AnatomyStructure; m: AnatomyMapMarker } => !!x.m);

  const selectedMarker = selectedSlug ? markerFor(selectedSlug) : null;
  const selectedDirty = selectedSlug ? !!edits[selectedSlug] : false;

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={t('anatomy.editor_title', { defaultValue: 'Position Editor' })}
      className="max-w-6xl"
    >
      {/* Figure selector + zoom */}
      <div className="flex items-center flex-wrap gap-2 mb-3">
        {figureOrder.map((slug) => {
          const f = figures[slug];
          if (!f) return null;
          return (
            <button
              key={slug}
              onClick={() => setFigureSlug(slug)}
              className={`px-3 py-1.5 text-xs font-black uppercase rounded-lg transition-colors ${
                figureSlug === slug
                  ? 'bg-blue-500 text-white'
                  : 'bg-gray-100 dark:bg-dark-bg text-gray-500 hover:bg-gray-200 dark:hover:bg-dark-border'
              }`}
            >
              {f.label}
            </button>
          );
        })}
        <span className="text-[10px] text-gray-400 ml-2">
          {t('anatomy.editor_hint', { defaultValue: 'Drag dots on the body. Changes auto-save.' })}
        </span>
        <div className="flex items-center gap-1 ml-auto bg-gray-100 dark:bg-dark-bg rounded-lg px-1.5 py-1">
          <button
            onClick={() => setZoom((z) => Math.max(0.5, +(z - 0.25).toFixed(2)))}
            className="p-1 rounded-md bg-white dark:bg-dark-surface text-gray-600 dark:text-dark-muted hover:text-blue-500 transition-colors"
            title={t('anatomy.editor_zoom_out', { defaultValue: 'Zoom out' })}
          >
            <ZoomOut className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => setZoom(1)}
            className="text-[10px] font-mono font-bold text-gray-600 dark:text-dark-muted hover:text-blue-500 w-9 text-center"
            title={t('anatomy.editor_zoom_reset', { defaultValue: 'Reset zoom' })}
          >
            {Math.round(zoom * 100)}%
          </button>
          <button
            onClick={() => setZoom((z) => Math.min(3, +(z + 0.25).toFixed(2)))}
            className="p-1 rounded-md bg-white dark:bg-dark-surface text-gray-600 dark:text-dark-muted hover:text-blue-500 transition-colors"
            title={t('anatomy.editor_zoom_in', { defaultValue: 'Zoom in' })}
          >
            <ZoomIn className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      <div className="flex gap-4 h-[68vh]">
        <div className="relative flex-1 min-w-0">
          <div
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerLeave={handlePointerUp}
            className="h-full flex justify-center items-start overflow-auto bg-gray-50 dark:bg-dark-bg rounded-2xl touch-none select-none cursor-crosshair custom-scrollbar"
          >
            {isLoading && (
              <div className="absolute inset-0 flex items-center justify-center z-10 pointer-events-none">
                <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
              </div>
            )}
            <div
              ref={imgWrapRef}
              className="relative flex-shrink-0 my-4"
              style={{
                height: `${zoom * 100}%`,
                aspectRatio: figure ? `${figure.width} / ${figure.height}` : undefined,
              }}
            >
              <BodySilhouette figureSlug={figureSlug} className="w-full h-full" dim overlayInteractive>
                {renderedMarkers.map(({ s, m }) => {
                  const { cx, cy, r } = markerToCanvas(figure, m);
                  const isSelected = s.slug === selectedSlug;
                  const dragging = draggingRef.current === s.slug;
                  return (
                    <g key={s.slug} style={{ cursor: dragging ? 'grabbing' : 'grab' }}>
                      <circle
                        cx={cx}
                        cy={cy}
                        r={r}
                        fill={isSelected ? '#3b82f6' : '#94a3b8'}
                        fillOpacity={isSelected ? 0.8 : 0.45}
                        stroke={isSelected ? '#1d4ed8' : '#64748b'}
                        strokeWidth={isSelected ? 3 : 1.5}
                        className={isSelected ? 'animate-pulse' : ''}
                        style={{ pointerEvents: 'all' }}
                        onPointerDown={(e) => {
                          e.preventDefault();
                          (e.target as Element).setPointerCapture?.(e.pointerId);
                          draggingRef.current = s.slug;
                          setSelectedSlug(s.slug);
                        }}
                      >
                        <title>{s.name}</title>
                      </circle>
                      {isSelected && (
                        <text
                          x={cx}
                          y={cy - r - 4}
                          textAnchor="middle"
                          className="fill-blue-600 dark:fill-blue-400"
                          style={{ fontSize: '11px', fontWeight: 'bold' }}
                          pointerEvents="none"
                        >
                          {s.name}
                        </text>
                      )}
                    </g>
                  );
                })}
              </BodySilhouette>
            </div>
          </div>
          {selectedMarker && (
            <div className="absolute bottom-3 left-3 right-3 bg-white/90 dark:bg-dark-surface/90 backdrop-blur rounded-xl px-3 py-2 text-xs flex flex-col gap-2 border border-gray-100 dark:border-dark-border z-20">
              <div className="flex items-center justify-between gap-2">
                <span className="font-bold text-gray-700 dark:text-dark-text truncate">
                  {structures.find((s) => s.slug === selectedSlug)?.name}
                </span>
                <span className="font-mono text-gray-400">
                  nx={selectedMarker.nx.toFixed(2)} ny={selectedMarker.ny.toFixed(2)}
                </span>
                {selectedDirty ? (
                  <span className="text-amber-500 font-bold">{t('anatomy.editor_unsaved', { defaultValue: 'Unsaved' })}</span>
                ) : (
                  <span className="text-green-500 flex items-center gap-1">
                    <Check className="w-3 h-3" /> {t('common.saved', { defaultValue: 'Saved' })}
                  </span>
                )}
                {saving.has(selectedSlug ?? '') && <Loader2 className="w-3 h-3 animate-spin text-blue-500" />}
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-black uppercase tracking-widest text-gray-400 flex-shrink-0">
                  {t('anatomy.editor_size', { defaultValue: 'Size' })}
                </span>
                <input
                  type="range"
                  min={1}
                  max={15}
                  step={0.5}
                  value={(selectedMarker.nr ?? 0.02) * 100}
                  onChange={(e) => selectedSlug && setMarkerSize(selectedSlug, Number(e.target.value) / 100)}
                  onPointerUp={() => selectedSlug && edits[selectedSlug] && saveMarker(selectedSlug)}
                  className="flex-1 accent-blue-500"
                />
                <span className="font-mono text-gray-400 w-8 text-right text-[10px]">
                  {Math.round((selectedMarker.nr ?? 0.02) * 100)}%
                </span>
              </div>
            </div>
          )}
        </div>

        <div className="w-72 flex-shrink-0 flex flex-col bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-2xl overflow-hidden">
          <div className="p-3 border-b border-gray-100 dark:border-dark-border">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t('anatomy.search_placeholder')}
                className="w-full pl-10 pr-3 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl text-sm outline-none focus:ring-2 focus:ring-blue-500/20 dark:text-dark-text"
              />
            </div>
          </div>
          <div className="flex-1 overflow-y-auto custom-scrollbar">
            {filtered.map((s) => {
              const has = !!markerFor(s.slug);
              const isSel = s.slug === selectedSlug;
              return (
                <button
                  key={s.id}
                  onClick={() => setSelectedSlug(s.slug)}
                  className={`w-full flex items-center justify-between px-3 py-2 text-sm transition-colors ${
                    isSel ? 'bg-blue-50 dark:bg-blue-900/20' : 'hover:bg-gray-50 dark:hover:bg-dark-bg'
                  }`}
                >
                  <span className="flex items-center gap-2 min-w-0">
                    <span
                      className="w-2 h-2 rounded-full flex-shrink-0"
                      style={{ background: CATEGORY_COLORS[s.category] ?? '#94a3b8' }}
                    />
                    <span className="text-gray-700 dark:text-dark-text truncate">{s.name}</span>
                  </span>
                  <span
                    role="button"
                    tabIndex={0}
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleMarker(s.slug);
                    }}
                    className={`flex-shrink-0 ml-2 text-[9px] font-black uppercase px-1.5 py-0.5 rounded ${
                      has
                        ? 'bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400'
                        : 'bg-gray-100 dark:bg-dark-bg text-gray-400'
                    }`}
                  >
                    {has ? (
                      <span className="flex items-center gap-0.5">
                        <Crosshair className="w-2.5 h-2.5" /> ON
                      </span>
                    ) : (
                      'OFF'
                    )}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </Modal>
  );
};
