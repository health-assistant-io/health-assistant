import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

export type PopoverSide = 'top' | 'bottom';
export type PopoverAlign = 'start' | 'center' | 'end';

export interface PopoverProps {
  /** Open state — controlled by the parent. */
  isOpen: boolean;
  /** Close callback (used for scroll/resize when reposition fails, and for outside-click). */
  onClose: () => void;
  /** Ref to the trigger element the popover anchors to. */
  triggerRef: React.RefObject<HTMLElement>;
  children: React.ReactNode;
  /** Preferred side relative to the trigger. Auto-flips if not enough room. */
  side?: PopoverSide;
  /** Horizontal alignment relative to the trigger. Auto-shifts if not enough room. */
  align?: PopoverAlign;
  /** Gap between trigger and popover, in px. */
  sideOffset?: number;
  /** Minimum distance from the viewport edge, in px. */
  viewportPadding?: number;
  /** Extra classes on the floating element. */
  className?: string;
  /** z-index (defaults to 9999 so the popover floats above modal panels). */
  zIndex?: number;
  /**
   * Behavior when a scroll event happens in any ancestor (capture phase).
   * - `'reposition'` (default): recompute coords on every scroll — popover tracks the trigger.
   * - `'close'`: call `onClose` on first scroll.
   */
  onScroll?: 'reposition' | 'close';
}

interface Coords {
  top: number;
  left: number;
}

const VIEWPORT_PADDING_DEFAULT = 8;

/**
 * Portal-rendered popover that anchors to a trigger element via
 * `getBoundingClientRect()` and renders at the top of `document.body`,
 * escaping any parent `overflow: hidden` / `overflow-y: auto` containers
 * (the most common cause of clipped dropdowns inside modal forms).
 *
 * Auto-flips when there's no room on the preferred side; auto-shifts when
 * the alignment would push past the viewport edge. Repositions on scroll
 * (capture phase, so nested scroll containers trigger it) and on resize.
 *
 * The reference implementation: `AIActionsDropdown.tsx` (which already uses
 * this pattern manually). This component generalises it.
 */
export const Popover: React.FC<PopoverProps> = ({
  isOpen,
  onClose,
  triggerRef,
  children,
  side = 'bottom',
  align = 'start',
  sideOffset = 4,
  viewportPadding = VIEWPORT_PADDING_DEFAULT,
  className = '',
  zIndex = 9999,
  onScroll = 'reposition',
}) => {
  const floatingRef = useRef<HTMLDivElement>(null);
  const [coords, setCoords] = useState<Coords>({ top: 0, left: 0 });
  // Hidden on first paint to avoid a flash at (0,0); shown after the first
  // layout effect computes the real position.
  const [hasPosition, setHasPosition] = useState(false);

  const computePosition = React.useCallback(() => {
    const trigger = triggerRef.current;
    const floating = floatingRef.current;
    if (!trigger) return;

    const trect = trigger.getBoundingClientRect();
    // The floating element is rendered with `visibility: hidden` until the
    // first position is computed, but it still has layout so we can read its
    // size via getBoundingClientRect — if it's not yet mounted (first run),
    // fall back to a sane default (250x320 — typical calendar/dropdown size).
    const frect = floating
      ? floating.getBoundingClientRect()
      : { width: 250, height: 320 };

    const vw = window.innerWidth;
    const vh = window.innerHeight;

    // --- Side (vertical) with auto-flip -----------------------------------
    let actualSide = side;
    const roomBelow = vh - trect.bottom;
    const roomAbove = trect.top;
    if (side === 'bottom' && roomBelow < frect.height + sideOffset + viewportPadding && roomAbove > roomBelow) {
      actualSide = 'top';
    } else if (side === 'top' && roomAbove < frect.height + sideOffset + viewportPadding && roomBelow > roomAbove) {
      actualSide = 'bottom';
    }

    // --- Align (horizontal) with auto-shift -------------------------------
    let top: number;
    let left: number;
    if (actualSide === 'bottom') {
      top = trect.bottom + sideOffset;
    } else {
      top = trect.top - frect.height - sideOffset;
    }

    // Base position from alignment.
    let shiftLeft: number;
    if (align === 'start') {
      shiftLeft = trect.left;
    } else if (align === 'center') {
      shiftLeft = trect.left + trect.width / 2 - frect.width / 2;
    } else {
      shiftLeft = trect.right - frect.width;
    }

    // Clamp into viewport.
    const minLeft = viewportPadding;
    const maxLeft = vw - frect.width - viewportPadding;
    left = Math.max(minLeft, Math.min(shiftLeft, maxLeft));

    // Final clamp so the popover never escapes the viewport vertically either
    // (if both sides ran out of room — e.g. tiny viewport — keep it on screen).
    top = Math.max(viewportPadding, Math.min(top, vh - frect.height - viewportPadding));

    setCoords({ top, left });
    setHasPosition(true);
  }, [triggerRef, side, align, sideOffset, viewportPadding]);

  // Recompute on mount/update of children (size may change between renders).
  useLayoutEffect(() => {
    if (!isOpen) {
      setHasPosition(false);
      return;
    }
    computePosition();
  }, [isOpen, computePosition, children]);

  // Track scroll (capture so nested scrollable forms trigger it) + resize.
  useEffect(() => {
    if (!isOpen) return;
    const handleScroll = () => {
      if (onScroll === 'close') {
        onClose();
      } else {
        computePosition();
      }
    };
    const handleResize = () => computePosition();
    window.addEventListener('scroll', handleScroll, true);
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('scroll', handleScroll, true);
      window.removeEventListener('resize', handleResize);
    };
  }, [isOpen, onScroll, onClose, computePosition]);

  // Outside-click close. Mousedown so we beat onClick handlers inside the
  // popover firing on the same tick (and so we don't run on text-selection).
  useEffect(() => {
    if (!isOpen) return;
    const handle = (e: MouseEvent) => {
      const t = e.target as Node;
      if (
        triggerRef.current?.contains(t) ||
        floatingRef.current?.contains(t)
      ) {
        return;
      }
      onClose();
    };
    // Use setTimeout(0) so the same click that opened the popover doesn't
    // immediately close it.
    const id = window.setTimeout(() => {
      document.addEventListener('mousedown', handle);
    }, 0);
    return () => {
      window.clearTimeout(id);
      document.removeEventListener('mousedown', handle);
    };
  }, [isOpen, onClose, triggerRef]);

  if (!isOpen) return null;

  return createPortal(
    <div
      ref={floatingRef}
      className={`fixed ${className}`}
      style={{
        top: `${coords.top}px`,
        left: `${coords.left}px`,
        zIndex,
        visibility: hasPosition ? 'visible' : 'hidden',
      }}
      onClick={(e) => e.stopPropagation()}
    >
      {children}
    </div>,
    document.body,
  );
};
