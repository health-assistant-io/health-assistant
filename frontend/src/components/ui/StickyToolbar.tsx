import React, { useEffect, useRef, useState } from 'react';

interface StickyToolbarProps {
  children?: React.ReactNode;
  actions?: React.ReactNode;
  details?: React.ReactNode;
  center?: React.ReactNode;
  sticky?: boolean;
  className?: string;
}

// Walk up the DOM to find the first actually-scrollable ancestor.
// (The app scrolls inside <main>, not the window — so we must attach the
// scroll listener to the real scroll container, not window.)
function getScrollParent(node: HTMLElement | null): HTMLElement | null {
  let parent = node?.parentElement;
  while (parent && parent !== document.body) {
    const { overflowY } = getComputedStyle(parent);
    if (
      (overflowY === 'auto' || overflowY === 'scroll') &&
      parent.scrollHeight > parent.clientHeight
    ) {
      return parent;
    }
    parent = parent.parentElement;
  }
  return null;
}

export const StickyToolbar: React.FC<StickyToolbarProps> = ({
  children,
  actions,
  details,
  center,
  sticky = true,
  className = '',
}) => {
  const toolbarRef = useRef<HTMLDivElement>(null);
  const [hidden, setHidden] = useState(false);
  // Last scroll position — used to detect direction (up vs down).
  const lastScrollTop = useRef(0);
  // Scroll position at which we last toggled visibility — used so a
  // sustained movement of THRESHOLD px in one direction is required
  // before toggling again. Prevents jitter on momentum / micro-scrolls.
  const lastToggle = useRef(0);

  useEffect(() => {
    if (!sticky) return;
    const el = toolbarRef.current;
    if (!el) return;
    const scrollParent = getScrollParent(el);
    if (!scrollParent) return;

    const DIRECTION_THRESHOLD = 12; // px of sustained scroll before toggling
    const TOP_ZONE = 80; // always reveal within this distance of the top

    const handleScroll = () => {
      const current = scrollParent.scrollTop;

      // Always reveal near the top of the page — hiding here is jarring
      // and the toolbar is part of the page's initial identity.
      if (current <= TOP_ZONE) {
        setHidden(false);
        lastToggle.current = current;
      } else if (current > lastScrollTop.current) {
        // Scrolling down — hide only after sustained downward movement.
        if (current - lastToggle.current > DIRECTION_THRESHOLD) {
          setHidden(true);
          lastToggle.current = current;
        }
      } else if (current < lastScrollTop.current) {
        // Scrolling up — reveal after sustained upward movement.
        if (lastToggle.current - current > DIRECTION_THRESHOLD) {
          setHidden(false);
          lastToggle.current = current;
        }
      }

      lastScrollTop.current = current;
    };

    // Seed refs on mount so we don't fire a spurious toggle on the first event.
    lastScrollTop.current = scrollParent.scrollTop;
    lastToggle.current = scrollParent.scrollTop;

    scrollParent.addEventListener('scroll', handleScroll, { passive: true });
    return () => scrollParent.removeEventListener('scroll', handleScroll);
  }, [sticky]);

  return (
    <div
      ref={toolbarRef}
      style={{
        // Slide up out of view when hidden. -100% of own height fully clears
        // the toolbar (its `top: -6px…-18px` is already above the viewport),
        // the extra -8px covers the bottom border + breathing room.
        transform: hidden && sticky ? 'translateY(calc(-100% - 8px))' : 'translateY(0)',
      }}
      className={`
        ${sticky ? 'sticky top-[-6px] md:top-[-14px] lg:top-[-18px] z-[450] backdrop-blur-md bg-gray-50/90 dark:bg-dark-bg/90 py-3 mb-6' : 'py-2 mb-4'}
        flex flex-wrap items-center justify-between gap-4 transition-all duration-300 border-b border-gray-200 dark:border-dark-border -mx-2 sm:-mx-4 md:-mx-6 lg:-mx-8 px-2 sm:px-4 md:px-6 lg:px-8
        ${className}
      `}
    >
      <div className="flex flex-wrap items-center gap-6 flex-1 min-w-0">
        {details && (
          <div className="flex items-center">
            {details}
          </div>
        )}

        {center && (
          <div className="flex-1 flex justify-center">
            {center}
          </div>
        )}

        {children}
      </div>

      {actions && (
        <div className="flex flex-wrap items-center gap-3 ml-auto">
          {actions}
        </div>
      )}
    </div>
  );
};

export default StickyToolbar;
