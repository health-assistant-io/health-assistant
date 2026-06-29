import { create } from 'zustand';
import { anatomyService } from '../../services/anatomyService';
import type { AnatomyStructure, AnatomyMapMarker, AnatomyFigure } from '../../types/anatomy';

/**
 * DB-driven body atlas backed by raster images. Figure metadata is fetched from
 * `/api/v1/anatomy/figures`; each figure's image is fetched as a blob (auth
 * handled by the axios interceptor) and cached as an object URL.
 *
 * Markers on AnatomyStructure are keyed by figure slug, normalized 0-1 against
 * the image's pixel dimensions (width × height).
 */

export interface AnatomyAtlasState {
  figures: Record<string, AnatomyFigure>;
  figureOrder: string[];
  imageUrls: Record<string, string>;
  loaded: boolean;
  loading: boolean;
  error: string | null;

  load: (force?: boolean) => Promise<void>;
  ensureLoaded: () => Promise<void>;
  getImage: (slug: string) => Promise<string | null>;
  reset: () => void;
}

export const useAnatomyAtlas = create<AnatomyAtlasState>((set, get) => ({
  figures: {},
  figureOrder: [],
  imageUrls: {},
  loaded: false,
  loading: false,
  error: null,

  load: async (force = false) => {
    if (get().loading) return;
    if (get().loaded && !force) return;
    set({ loading: true, error: null });
    try {
      const list = await anatomyService.listFigures(true);
      const figures: Record<string, AnatomyFigure> = {};
      for (const f of list) figures[f.slug] = f;
      // On a force-reload (after an image was edited/replaced), revoke and
      // clear the cached blob URLs so stale images are re-fetched fresh.
      const imageUrls = force ? {} : get().imageUrls;
      if (force) {
        Object.values(get().imageUrls).forEach((u) => URL.revokeObjectURL(u));
      }
      set({ figures, figureOrder: list.map((f) => f.slug), imageUrls, loaded: true, loading: false });
    } catch (e: any) {
      set({ loading: false, error: e?.message ?? 'Failed to load atlas' });
    }
  },

  ensureLoaded: async () => {
    if (!get().loaded) await get().load();
  },

  getImage: async (slug: string) => {
    const cached = get().imageUrls[slug];
    if (cached) return cached;
    const url = await anatomyService.fetchFigureImage(slug);
    if (url) set((s) => ({ imageUrls: { ...s.imageUrls, [slug]: url } }));
    return url;
  },

  reset: () => {
    const urls = get().imageUrls;
    Object.values(urls).forEach((u) => URL.revokeObjectURL(u));
    set({ figures: {}, figureOrder: [], imageUrls: {}, loaded: false, loading: false, error: null });
  },
}));

/** Resolve a structure's marker for a given figure slug. */
export function getMarker(structure: AnatomyStructure, figureSlug: string): AnatomyMapMarker | null {
  return structure.display?.map?.markers?.[figureSlug] ?? null;
}

/** First figure slug (in display order) that has a marker for this structure. */
export function firstMarkerFigure(structure: AnatomyStructure, order: string[]): string | null {
  const markers = structure.display?.map?.markers;
  if (!markers) return null;
  for (const slug of order) if (markers[slug]) return slug;
  const first = Object.keys(markers)[0];
  return first ?? null;
}

/** Resolve the (figureSlug, marker) pair to display for a structure. */
export function markerForStructure(
  structure: AnatomyStructure | null | undefined,
  order: string[],
): { figureSlug: string; marker: AnatomyMapMarker | null } {
  const fallback = order[0] ?? 'man-front';
  if (!structure) return { figureSlug: fallback, marker: null };
  const slug = firstMarkerFigure(structure, order);
  if (slug) return { figureSlug: slug, marker: getMarker(structure, slug) };
  return { figureSlug: fallback, marker: null };
}

/** Group figures by figure_key, preserving sort_order within each group. */
export function figuresByGroup(figures: Record<string, AnatomyFigure>): Record<string, AnatomyFigure[]> {
  const groups: Record<string, AnatomyFigure[]> = {};
  const sorted = Object.values(figures).sort((a, b) => a.sort_order - b.sort_order);
  for (const f of sorted) {
    (groups[f.figure_key] ??= []).push(f);
  }
  return groups;
}
