/**
 * TimePicker — clock-face time selector with a 12h AM/PM UI.
 *
 * - Stored value is always a 24-hour `HH:MM` string (DB/native compatible).
 * - The trigger + popover render a 12-hour face with an AM/PM toggle for ergonomics.
 * - Reuses the portal-based `<Popover>` so it escapes `overflow: hidden` modals.
 *
 * Usage:
 *   <TimePicker value="14:30" onChange={setTime} />
 *   <TimePicker value={null} onChange={setTime} placeholder="Select time" />
 */
import React, { useMemo, useRef, useState, useEffect, useCallback } from 'react';
import { Clock as ClockIcon, Check } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Popover } from './Popover';

type ViewMode = 'hours' | 'minutes';

export interface TimePickerProps {
  /** 24-hour `HH:MM` string, or null/undefined when empty. */
  value: string | null | undefined;
  /** Fired with a 24-hour `HH:MM` string. */
  onChange: (value: string) => void;
  className?: string;
  placeholder?: string;
  disabled?: boolean;
  id?: string;
  /** Diameter of the clock face in px (default 240). */
  size?: number;
  /** Optional label rendered above the trigger. */
  label?: string;
  variant?: 'default' | 'unstyled';
}

/* ----------------------------- value helpers ----------------------------- */

interface Hms {
  hour24: number; // 0–23
  minute: number; // 0–59
}

function parse24(value: string | null | undefined): Hms | null {
  if (!value) return null;
  const m = /^(\d{1,2}):(\d{1,2})$/.exec(value.trim());
  if (!m) return null;
  const h = parseInt(m[1], 10);
  const min = parseInt(m[2], 10);
  if (Number.isNaN(h) || Number.isNaN(min) || h < 0 || h > 23 || min < 0 || min > 59) return null;
  return { hour24: h, minute: min };
}

function to24(hour12: number, period: 'AM' | 'PM'): number {
  if (period === 'AM') return hour12 === 12 ? 0 : hour12;
  return hour12 === 12 ? 12 : hour12 + 12;
}

function from24(hour24: number): { hour12: number; period: 'AM' | 'PM' } {
  if (hour24 === 0) return { hour12: 12, period: 'AM' };
  if (hour24 === 12) return { hour12: 12, period: 'PM' };
  if (hour24 < 12) return { hour12: hour24, period: 'AM' };
  return { hour12: hour24 - 12, period: 'PM' };
}

function format12(hour24: number, minute: number): string {
  const { hour12, period } = from24(hour24);
  const mm = minute.toString().padStart(2, '0');
  return `${hour12}:${mm} ${period}`;
}

function format24(hour24: number, minute: number): string {
  return `${hour24.toString().padStart(2, '0')}:${minute.toString().padStart(2, '0')}`;
}

/* --------------------------------- clock --------------------------------- */

interface ClockProps {
  mode: ViewMode;
  hour12: number; // 1–12
  minute: number; // 0–59
  size: number;
  onPickHour: (h: number) => void;
  onPickMinute: (m: number) => void;
}

/**
 * SVG clock face. Hours (1–12) and minutes (0,5,…,55) sit on a ring; the
 * selected tick is highlighted and a hand points to it. Click anywhere on the
 * face to snap to the nearest tick of the active mode. AM/PM lives outside —
 * kept entirely off the dial so it can never be clipped.
 */
const ClockFace: React.FC<ClockProps> = ({
  mode,
  hour12,
  minute,
  size,
  onPickHour,
  onPickMinute,
}) => {
  const cx = size / 2;
  const cy = size / 2;
  const radius = size / 2 - 22; // padding for the tick circles

  const ticks =
    mode === 'hours'
      ? Array.from({ length: 12 }, (_, i) => i + 1) // 1..12
      : Array.from({ length: 12 }, (_, i) => i * 5); // 0,5,..,55

  const selectedValue = mode === 'hours' ? hour12 : minute;

  const angleFor = useCallback((val: number) => {
    // 12 at the top, clockwise. Hour 12 → -90deg, hour 3 → 0deg, etc.
    if (mode === 'hours') {
      return (val / 12) * 360 - 90;
    }
    return (val / 60) * 360 - 90;
  }, [mode]);

  const pointFor = useCallback(
    (val: number, r: number) => {
      const rad = (angleFor(val) * Math.PI) / 180;
      return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
    },
    [angleFor, cx, cy],
  );

  const handTarget = pointFor(selectedValue, radius - 22);
  const labelPoint = (val: number) => pointFor(val, radius);

  const handleClick = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = e.currentTarget;
    const rect = svg.getBoundingClientRect();
    const x = e.clientX - rect.left - cx;
    const y = e.clientY - rect.top - cy;
    let deg = (Math.atan2(y, x) * 180) / Math.PI + 90; // 0 at top, CW
    if (deg < 0) deg += 360;
    if (mode === 'hours') {
      const h = Math.round(deg / 30) || 12;
      onPickHour(h);
    } else {
      const m = (Math.round(deg / 30) || 12) * 5;
      onPickMinute(m === 60 ? 0 : m);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<SVGSVGElement>) => {
    if (mode === 'hours') {
      if (e.key === 'ArrowRight' || e.key === 'ArrowUp') {
        e.preventDefault();
        onPickHour(hour12 >= 12 ? 1 : hour12 + 1);
      } else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') {
        e.preventDefault();
        onPickHour(hour12 <= 1 ? 12 : hour12 - 1);
      }
    } else {
      if (e.key === 'ArrowRight' || e.key === 'ArrowUp') {
        e.preventDefault();
        onPickMinute(minute >= 55 ? 0 : minute + 5);
      } else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') {
        e.preventDefault();
        onPickMinute(minute <= 0 ? 55 : minute - 5);
      }
    }
  };

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      tabIndex={0}
      role="slider"
      aria-label={mode === 'hours' ? 'Hour' : 'Minute'}
      aria-valuemin={mode === 'hours' ? 1 : 0}
      aria-valuemax={mode === 'hours' ? 12 : 59}
      aria-valuenow={selectedValue}
      className="outline-none focus:ring-2 focus:ring-blue-500/40 rounded-full cursor-pointer"
    >
      {/* Dial */}
      <circle
        cx={cx}
        cy={cy}
        r={radius + 4}
        className="fill-gray-50 dark:bg-dark-bg stroke-gray-100 dark:stroke-dark-border"
        strokeWidth={1}
      />
      <circle cx={cx} cy={cy} r={radius + 4} className="fill-gray-50 dark:fill-gray-900" />

      {/* Ticks + labels */}
      {ticks.map((val) => {
        const p = labelPoint(val);
        const isSelected = val === selectedValue;
        return (
          <g key={val}>
            <circle
              cx={p.x}
              cy={p.y}
              r={isSelected ? 17 : 15}
              className={
                isSelected
                  ? 'fill-blue-600 dark:fill-blue-500 transition-all'
                  : 'fill-white dark:fill-gray-800 stroke-gray-100 dark:stroke-gray-700 transition-all'
              }
              strokeWidth={1}
            />
            <text
              x={p.x}
              y={p.y}
              textAnchor="middle"
              dominantBaseline="central"
              className={
                isSelected
                  ? 'fill-white font-bold'
                  : 'fill-gray-700 dark:fill-gray-200 font-medium'
              }
              style={{ fontSize: 13, userSelect: 'none' }}
            >
              {val.toString().padStart(2, '0')}
            </text>
          </g>
        );
      })}

      {/* Hand */}
      <line
        x1={cx}
        y1={cy}
        x2={handTarget.x}
        y2={handTarget.y}
        className="stroke-blue-600 dark:stroke-blue-500"
        strokeWidth={2}
        strokeLinecap="round"
      />
      <circle cx={cx} cy={cy} r={5} className="fill-blue-600 dark:fill-blue-500" />
    </svg>
  );
};

/* --------------------------- editable number box -------------------------- */

interface TimeInputProps {
  value: number;
  min: number;
  max: number;
  /** Active mode highlight (blue text). */
  active?: boolean;
  onChange: (n: number) => void;
  onFocus: () => void;
  /**
   * Optional 24-hour normalization hook (used by the hours field). When
   * provided, values `> max` (e.g. 14) or `=== 0` are treated as 24-hour
   * input and routed through this callback instead of being clamped —
   * letting the parent flip AM/PM and set the 12h hour together.
   */
  onConvertFrom24?: (n24: number) => void;
}

/**
 * A 2-digit, keyboard-editable numeric field. Accepts only digits, clamps on
 * blur/Enter, and supports Arrow Up/Down to step. While focused it holds the
 * raw text so the user can clear/replace freely; it re-syncs from `value`
 * when not focused (so clock clicks still update the field).
 */
const TimeInput: React.FC<TimeInputProps> = ({ value, min, max, active, onChange, onFocus, onConvertFrom24 }) => {
  const pad = (n: number) => n.toString().padStart(2, '0');
  const [text, setText] = useState<string>(pad(value));
  const [focused, setFocused] = useState(false);

  useEffect(() => {
    if (!focused) setText(pad(value));
  }, [value, focused]);

  const commit = (raw: string) => {
    const digits = raw.replace(/\D/g, '');
    if (digits === '') {
      setText(pad(value));
      return;
    }
    const n = parseInt(digits, 10);
    if (Number.isNaN(n)) {
      setText(pad(value));
      return;
    }
    // 24h conversion path (hour field only): typing 13–23 or 0 normalizes to
    // 1–12 and toggles AM/PM. Values 1–12 stay in the normal-clamp path so
    // the period isn't disturbed.
    if (onConvertFrom24 && (n > max || n === 0)) {
      onConvertFrom24(n);
      return;
    }
    const clamped = Math.max(min, Math.min(max, n));
    onChange(clamped);
    setText(pad(clamped));
  };

  return (
    <input
      type="text"
      inputMode="numeric"
      pattern="[0-9]*"
      maxLength={2}
      value={text}
      aria-label={active ? 'Selected field' : undefined}
      onFocus={() => {
        setFocused(true);
        onFocus();
        // Select-all on focus so typing replaces immediately.
        requestAnimationFrame(() => {
          const el = document.activeElement as HTMLInputElement | null;
          if (el) el.select();
        });
      }}
      onBlur={() => {
        setFocused(false);
        commit(text);
      }}
      onChange={(e) => setText(e.target.value.replace(/\D/g, '').slice(0, 2))}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          (e.target as HTMLInputElement).blur();
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          const next = value >= max ? min : value + 1;
          onChange(next);
        } else if (e.key === 'ArrowDown') {
          e.preventDefault();
          const next = value <= min ? max : value - 1;
          onChange(next);
        }
      }}
      className={`w-12 h-10 text-center text-2xl font-bold tabular-nums bg-transparent outline-none rounded-md transition-colors ${
        active
          ? 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/30'
          : 'text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-dark-hover'
      }`}
    />
  );
};

/* --------------------------- picker body (reusable) ----------------------- */

export interface TimePickerContentProps {
  /** 24-hour `HH:MM` string, or null/undefined when empty. */
  value: string | null | undefined;
  /** Fired with a 24-hour `HH:MM` string on every change. */
  onChange: (value: string) => void;
  /** Fired when the user clicks the Done button. */
  onDone?: () => void;
  /** Diameter of the clock face in px (default 240). */
  size?: number;
  /** Optional className on the outer panel. */
  className?: string;
}

/**
 * The popover body: editable HH:MM inputs + AM/PM segmented control + clock
 * face + Done button. Owns its own "working copy" of the time.
 *
 * The seed is read from `value` ONCE on mount. This is by design: in both
 * use sites (the `<TimePicker>` trigger and the chip popovers in `<TimeList>`),
 * the surrounding `<Popover>` returns null when closed — so this component
 * unmounts on close and remounts fresh each time the popover opens, picking up
 * the latest `value` automatically. No re-seed effect is needed (and one
 * would be harmful: an inline edit calls onChange → new value prop → effect
 * re-fires → mode resets to 'hours' mid-interaction).
 */
export const TimePickerContent: React.FC<TimePickerContentProps> = ({
  value,
  onChange,
  onDone,
  size = 240,
  className = '',
}) => {
  const { t } = useTranslation();

  // Compute the seed once (mount-only). useRef + lazy init avoids both a
  // useEffect and a recompute per render.
  const seedRef = useRef<{ h24: number; min: number } | null>(null);
  if (seedRef.current === null) {
    const p = parse24(value);
    if (p) {
      seedRef.current = { h24: p.hour24, min: p.minute };
    } else {
      const now = new Date();
      seedRef.current = { h24: now.getHours(), min: now.getMinutes() };
    }
  }
  const seed = seedRef.current;

  const [hour12, setHour12] = useState<number>(() => from24(seed.h24).hour12);
  const [minute, setMinute] = useState<number>(seed.min);
  const [period, setPeriod] = useState<'AM' | 'PM'>(() => from24(seed.h24).period);
  const [mode, setMode] = useState<ViewMode>('hours');

  const commit = useCallback(
    (h12: number, min: number, p: 'AM' | 'PM') => {
      onChange(format24(to24(h12, p), min));
    },
    [onChange],
  );

  const pickHour = (h: number) => {
    setHour12(h);
    commit(h, minute, period);
  };
  const pickMinute = (m: number) => {
    setMinute(m);
    commit(hour12, m, period);
  };
  const togglePeriod = (p: 'AM' | 'PM') => {
    if (p === period) return;
    setPeriod(p);
    commit(hour12, minute, p);
  };

  return (
    <div className={`p-4 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-2xl shadow-xl ${className}`}>
      {/* Top row: editable HH : MM + AM/PM segmented control */}
      <div className="flex items-center justify-center gap-2 mb-2 select-none">
        <div className="flex items-center gap-0.5 bg-gray-50 dark:bg-dark-bg rounded-lg px-1 py-0.5">
          <TimeInput
            value={hour12}
            min={1}
            max={12}
            active={mode === 'hours'}
            onChange={(h) => { setHour12(h); commit(h, minute, period); }}
            onConvertFrom24={(n24) => {
              const f = from24(n24);
              setHour12(f.hour12);
              setPeriod(f.period);
              commit(f.hour12, minute, f.period);
            }}
            onFocus={() => setMode('hours')}
          />
          <span className="text-2xl font-bold text-gray-400 px-0.5">:</span>
          <TimeInput
            value={minute}
            min={0}
            max={59}
            active={mode === 'minutes'}
            onChange={(m) => { setMinute(m); commit(hour12, m, period); }}
            onFocus={() => setMode('minutes')}
          />
        </div>

        {/* AM/PM segmented control */}
        <div className="flex rounded-lg overflow-hidden border border-gray-200 dark:border-dark-border">
          {(['AM', 'PM'] as const).map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => togglePeriod(p)}
              aria-pressed={period === p}
              className={`px-3 py-1.5 text-xs font-bold uppercase tracking-widest transition-colors ${
                period === p
                  ? 'bg-blue-600 text-white'
                  : 'bg-white dark:bg-dark-bg text-gray-500 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-dark-hover'
              }`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      {/* Mode hint */}
      <p className="text-center text-[10px] font-bold uppercase tracking-widest text-gray-400 mb-2">
        {mode === 'hours' ? t('common.hour', 'Hour') : t('common.minute', 'Minute')}
      </p>

      {/* Clock */}
      <div className="flex justify-center mb-3">
        <ClockFace
          mode={mode}
          hour12={hour12}
          minute={minute}
          size={size}
          onPickHour={pickHour}
          onPickMinute={pickMinute}
        />
      </div>

      {/* Confirm / close */}
      <div className="flex justify-end pt-2 border-t border-gray-100 dark:border-dark-border">
        <button
          type="button"
          onClick={onDone}
          className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-xs font-bold rounded-lg transition-colors"
        >
          <Check className="w-3.5 h-3.5" />
          {t('common.done', 'Done')}
        </button>
      </div>
    </div>
  );
};

/* ------------------------------ main component ----------------------------- */

export const TimePicker: React.FC<TimePickerProps> = ({
  value,
  onChange,
  className = '',
  placeholder,
  disabled,
  id,
  size = 240,
  label,
  variant = 'default',
}) => {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const triggerRef = useRef<HTMLDivElement>(null);

  const parsed = useMemo(() => parse24(value), [value]);
  const display = parsed ? format12(parsed.hour24, parsed.minute) : null;

  return (
    <div className={`relative ${className.includes('w-') ? '' : 'w-full'}`} ref={triggerRef}>
      {label && (
        <label className="block text-[10px] font-bold text-gray-400 uppercase tracking-widest mb-1.5 ml-1">
          {label}
        </label>
      )}
      <div
        onClick={() => !disabled && setIsOpen((o) => !o)}
        className={`
          flex items-center transition-all text-left cursor-pointer
          ${className.includes('w-') ? '' : 'w-full'}
          ${variant === 'default' ? 'px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus-within:ring-2 focus-within:ring-blue-500 hover:border-blue-300 dark:hover:border-blue-700' : ''}
          ${disabled ? 'opacity-50 cursor-not-allowed' : ''}
          ${className}
        `}
      >
        {variant === 'default' && (
          <ClockIcon className="w-5 h-5 text-gray-400 dark:text-gray-500 mr-2 flex-shrink-0" />
        )}
        <span
          className={`flex-1 overflow-hidden text-ellipsis whitespace-nowrap ${
            !parsed
              ? variant === 'default'
                ? 'text-gray-400'
                : 'opacity-70'
              : variant === 'default'
              ? 'text-gray-900 dark:text-dark-text font-medium'
              : ''
          }`}
        >
          {display || placeholder || t('common.select_time', 'Select time')}
        </span>
      </div>

      <input type="hidden" value={value || ''} id={id} name={id} />

      <Popover
        isOpen={isOpen && !disabled}
        onClose={() => setIsOpen(false)}
        triggerRef={triggerRef}
        side="bottom"
        align="start"
        sideOffset={4}
        className="w-[300px]"
      >
        <TimePickerContent
          value={value}
          onChange={onChange}
          onDone={() => setIsOpen(false)}
          size={size}
        />
      </Popover>
    </div>
  );
};
