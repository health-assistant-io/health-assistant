/**
 * Right-click context menu for a graph node — a Portaled, cursor-anchored
 * menu that surfaces the same actions as the detail card
 * ({@link GraphNodeActions}, menu variant). Closes on outside-click,
 * Escape, or after an action fires.
 *
 * Wired into {@link ConceptGraphView} via ReactFlow's ``onNodeContextMenu``.
 */
import React, { useEffect, useRef } from 'react';
import { Portal } from '../ui/Portal';
import { GraphNodeActions } from './GraphNodeActions';

interface GraphNodeContextMenuProps {
  /** Cursor position (client coordinates). */
  x: number;
  y: number;
  type: string;
  id: string;
  onClose: () => void;
  /** Centers the graph on the node (also closes the menu). */
  onFocus?: () => void;
}

export const GraphNodeContextMenu: React.FC<GraphNodeContextMenuProps> = ({
  x,
  y,
  type,
  id,
  onClose,
  onFocus,
}) => {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    const handleOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    // mousedown (not click) so the menu closes before a drag/selection starts.
    document.addEventListener('mousedown', handleOutside);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.removeEventListener('mousedown', handleOutside);
    };
  }, [onClose]);

  // Clamp so the menu never overflows the viewport.
  const left = Math.min(x, window.innerWidth - 220);
  const top = Math.min(y, window.innerHeight - 160);

  return (
    <Portal>
      <div
        ref={ref}
        className="context-menu-root fixed z-modal w-[200px] rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 shadow-xl overflow-hidden animate-in fade-in zoom-in-95 duration-100"
        style={{ left, top }}
      >
        <GraphNodeActions
          type={type}
          id={id}
          variant="menu"
          onFocus={() => {
            onFocus?.();
            onClose();
          }}
        />
      </div>
    </Portal>
  );
};
