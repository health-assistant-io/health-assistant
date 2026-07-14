import { useState, useEffect, useCallback } from 'react';

/**
 * Returns whether a CSS media query currently matches.
 * Re-evaluates on resize / orientation change.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window !== 'undefined') {
      return window.matchMedia(query).matches;
    }
    return false;
  });

  useEffect(() => {
    const mql = window.matchMedia(query);
    const handler = (e: MediaQueryListEvent) => setMatches(e.matches);

    setMatches(mql.matches);
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, [query]);

  return matches;
}

/** True when viewport width < 640px (Tailwind `sm` breakpoint). */
export function useIsMobile(): boolean {
  return useMediaQuery('(max-width: 639px)');
}

/** True when viewport width < 1024px (Tailwind `lg` breakpoint). */
export function useIsTablet(): boolean {
  return useMediaQuery('(max-width: 1023px)');
}

/** True when viewport width >= 1024px (Tailwind `lg` breakpoint). */
export function useIsDesktop(): boolean {
  return useMediaQuery('(min-width: 1024px)');
}

export type Breakpoint = 'xs' | 'sm' | 'md' | 'lg' | 'xl';

const BREAKPOINT_PX: Record<Breakpoint, number> = {
  xs: 480,
  sm: 640,
  md: 768,
  lg: 1024,
  xl: 1280,
};

/**
 * Returns the current Tailwind breakpoint based on `window.innerWidth`.
 * Useful when you need the actual breakpoint name rather than a boolean.
 */
export function useBreakpoint(): Breakpoint {
  const compute = useCallback((): Breakpoint => {
    if (typeof window === 'undefined') return 'lg';
    const w = window.innerWidth;
    if (w < BREAKPOINT_PX.xs) return 'xs';
    if (w < BREAKPOINT_PX.sm) return 'sm';
    if (w < BREAKPOINT_PX.md) return 'md';
    if (w < BREAKPOINT_PX.lg) return 'lg';
    if (w < BREAKPOINT_PX.xl) return 'lg';
    return 'xl';
  }, []);

  const [bp, setBp] = useState<Breakpoint>(compute);

  useEffect(() => {
    const handler = () => setBp(compute());
    window.addEventListener('resize', handler);
    return () => window.removeEventListener('resize', handler);
  }, [compute]);

  return bp;
}
