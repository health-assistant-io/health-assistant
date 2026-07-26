import React from 'react';

interface SetupProgressRingProps {
  /** Progress value, 0.0–1.0. */
  value: number;
  /** Diameter in px. */
  size?: number;
  /** Stroke width in px. */
  stroke?: number;
  /** Optional center label override (defaults to the percentage). */
  label?: string;
  className?: string;
}

/**
 * Circular SVG progress ring for the setup wizard.
 *
 * Renders the mandatory-completion ratio (`SetupChecklist.completion`) as
 * an indigo arc on a slate track, with the percentage in the centre.
 * Reused by the wizard header, the PatientDetail completion card, and the
 * header role-completion badge.
 *
 * Pure presentational — no data fetching, no i18n (the caller localises
 * `label` if it overrides the default percentage).
 */
export const SetupProgressRing: React.FC<SetupProgressRingProps> = ({
  value,
  size = 64,
  stroke = 6,
  label,
  className = '',
}) => {
  const ratio = Math.max(0, Math.min(1, value));
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - ratio);
  const pct = Math.round(ratio * 100);
  const done = ratio >= 1;
  const center = size / 2;

  return (
    <div className={`relative inline-flex items-center justify-center ${className}`} style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          strokeWidth={stroke}
          className="text-gray-200 dark:text-dark-border"
          stroke="currentColor"
        />
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          strokeWidth={stroke}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          className={done ? 'text-green-500' : 'text-blue-600'}
          stroke="currentColor"
          style={{ transition: 'stroke-dashoffset 0.4s ease' }}
        />
      </svg>
      <span
        className="absolute text-[11px] font-bold text-brand-navy dark:text-dark-text tabular-nums"
        style={{ fontSize: Math.max(10, size / 5) }}
      >
        {label ?? `${pct}%`}
      </span>
    </div>
  );
};

export default SetupProgressRing;
