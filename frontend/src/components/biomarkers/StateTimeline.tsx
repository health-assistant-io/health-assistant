/**
 * StateTimeline — categorical biomarker visualization (Positive / Negative /
 * Detected / ...) over time.
 *
 * State biomarkers don't have numeric values, so a line chart doesn't work.
 * This component renders a horizontal step chart:
 *
 *  - X axis: time (chronological observations).
 *  - Y axis: each unique state value is a categorical band.
 *  - Each observation is a colored dot placed on its state's band; adjacent
 *    same-state observations are connected by a step line so runs of the
 *    same value read as a continuous segment.
 *
 * Plus a quick-legend of the biomarker's allowed_states with their
 * normal/abnormal coloring so users can decode the dots at a glance.
 *
 * Data contract (mirrors the backend trends response after the
 * state-biomarkers fix):
 *   { date: string, state?: string|null, state_display?: string|null,
 *     state_is_normal?: boolean|null, value?: any }[]
 *
 * If the array is empty, callers should render their own empty state.
 */
import React, { useMemo } from 'react';
import { Check, AlertCircle } from 'lucide-react';

interface StateTimelineProps {
  /** Trend points sorted chronologically (oldest first is recommended). */
  points: any[];
  /** Optional height CSS value (default 400px). */
  height?: number | string;
}

/** Resolve a single observation to { label, isNormal, code }. */
function resolvePoint(p: any): { label: string; isNormal: boolean | null; code: string | null } {
  const code = (p.state ?? p.state_code ?? null) as string | null;
  const label = (p.state_display ?? p.value ?? code ?? '—') as string;
  let isNormal: boolean | null = null;
  if (p.state_is_normal === true) isNormal = true;
  else if (p.state_is_normal === false) isNormal = false;
  return { label, isNormal, code };
}

/** Pick tailwind classes for a state, branched on normal/abnormal. */
function toneClass(isNormal: boolean | null): {
  dot: string;
  line: string;
  legend: string;
} {
  if (isNormal === true) {
    return {
      dot: 'fill-emerald-500 stroke-emerald-500',
      line: 'stroke-emerald-400',
      legend: 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-300 dark:border-emerald-800/50',
    };
  }
  if (isNormal === false) {
    return {
      dot: 'fill-rose-500 stroke-rose-500',
      line: 'stroke-rose-400',
      legend: 'bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-900/30 dark:text-rose-300 dark:border-rose-800/50',
    };
  }
  return {
    dot: 'fill-gray-400 stroke-gray-400',
    line: 'stroke-gray-300',
    legend: 'bg-gray-50 text-gray-600 border-gray-200 dark:bg-dark-bg dark:text-dark-muted dark:border-dark-border',
  };
}

export const StateTimeline: React.FC<StateTimelineProps> = ({ points, height = 400 }) => {
  // Build the unique state bands in first-seen order so the legend matches
  // the order the patient actually experienced them.
  const bands = useMemo(() => {
    const seen = new Map<string, { label: string; isNormal: boolean | null; code: string | null }>();
    for (const p of points) {
      const r = resolvePoint(p);
      const key = r.code ?? r.label;
      if (!seen.has(key)) seen.set(key, r);
    }
    return Array.from(seen.values());
  }, [points]);

  const resolved = useMemo(() => points.map(resolvePoint), [points]);

  // Layout constants.
  const padX = 48;
  const padY = 32;
  const innerH = 320; // plot area height
  const plotTop = padY;
  const plotBottom = padY + innerH;

  // Time → x mapping (chronological).
  const timestamps = points.map((p) => new Date(p.date).getTime());
  const tMin = timestamps.length ? Math.min(...timestamps) : 0;
  const tMax = timestamps.length ? Math.max(...timestamps) : 1;
  const span = Math.max(1, tMax - tMin);

  // Compute pixel width based on container — we use a responsive viewBox.
  // 800px is the reference width; the SVG scales to container width.
  const W = 800;
  const innerW = W - padX * 2;
  const xAt = (t: number) => padX + ((t - tMin) / span) * innerW;

  // State → y mapping (one row per band).
  const bandHeight = bands.length > 0 ? innerH / bands.length : innerH;
  const yAt = (label: string) => {
    const idx = bands.findIndex((b) => (b.code ?? b.label) === label);
    return plotTop + (idx + 0.5) * bandHeight;
  };

  if (points.length === 0) {
    return null;
  }

  // Build the step-line path segments (one per consecutive pair).
  const segments: React.ReactNode[] = [];
  for (let i = 1; i < resolved.length; i++) {
    const prev = resolved[i - 1];
    const cur = resolved[i];
    const x1 = xAt(timestamps[i - 1]);
    const x2 = xAt(timestamps[i]);
    const y1 = yAt(prev.code ?? prev.label);
    const y2 = yAt(cur.code ?? cur.label);
    const cls = toneClass(cur.isNormal).line;
    segments.push(
      <path
        key={`seg-${i}`}
        d={`M ${x1} ${y1} H ${x2} V ${y2}`}
        fill="none"
        className={cls}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      />,
    );
  }

  return (
    <div className="space-y-4" style={{ minHeight: typeof height === 'number' ? height : undefined }}>
      {/* Legend */}
      {bands.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {bands.map((b) => {
            const cls = toneClass(b.isNormal);
            return (
              <span
                key={b.code ?? b.label}
                className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold border ${cls.legend}`}
              >
                {b.isNormal === true && <Check className="w-3 h-3" />}
                {b.isNormal === false && <AlertCircle className="w-3 h-3" />}
                <span>{b.label}</span>
                {b.code && b.code !== b.label && (
                  <span className="font-mono opacity-60">· {b.code}</span>
                )}
              </span>
            );
          })}
        </div>
      )}

      {/* Timeline SVG */}
      <svg
        viewBox={`0 0 ${W} ${plotBottom + padY}`}
        className="w-full"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label="State timeline"
      >
        {/* Horizontal band guides + labels */}
        {bands.map((b, i) => {
          const y = plotTop + (i + 0.5) * bandHeight;
          return (
            <g key={`band-${b.code ?? b.label}`}>
              <line
                x1={padX}
                x2={W - padX}
                y1={y}
                y2={y}
                className="stroke-gray-100 dark:stroke-dark-border"
                strokeWidth={1}
                strokeDasharray="3 3"
              />
              <text
                x={padX - 8}
                y={y + 4}
                textAnchor="end"
                className="fill-gray-500 dark:fill-dark-muted"
                style={{ fontSize: 11, fontWeight: 600 }}
              >
                {b.label.length > 18 ? `${b.label.slice(0, 18)}…` : b.label}
              </text>
            </g>
          );
        })}

        {/* Step-line connectors */}
        {segments}

        {/* Per-observation dots + tooltips (title element). */}
        {resolved.map((r, i) => {
          const x = xAt(timestamps[i]);
          const y = yAt(r.code ?? r.label);
          const cls = toneClass(r.isNormal);
          return (
            <g key={`pt-${i}`}>
              <circle
                cx={x}
                cy={y}
                r={6}
                className={cls.dot}
                strokeWidth={2}
              >
                <title>
                  {`${new Date(points[i].date).toLocaleString()} — ${r.label}` +
                    (r.isNormal === true ? ' (normal)' : r.isNormal === false ? ' (abnormal)' : '')}
                </title>
              </circle>
            </g>
          );
        })}

        {/* X-axis: first + last date as anchor labels. */}
        <text
          x={padX}
          y={plotBottom + 20}
          className="fill-gray-400 dark:fill-dark-muted"
          style={{ fontSize: 10, fontWeight: 700 }}
        >
          {new Date(tMin).toLocaleDateString()}
        </text>
        <text
          x={W - padX}
          y={plotBottom + 20}
          textAnchor="end"
          className="fill-gray-400 dark:fill-dark-muted"
          style={{ fontSize: 10, fontWeight: 700 }}
        >
          {new Date(tMax).toLocaleDateString()}
        </text>
      </svg>

      <p className="text-[10px] text-gray-400 dark:text-dark-muted italic px-1">
        {resolved.length} observation{resolved.length === 1 ? '' : 's'} ·{' '}
        {bands.length} unique state{bands.length === 1 ? '' : 's'}
      </p>
    </div>
  );
};

export default StateTimeline;
