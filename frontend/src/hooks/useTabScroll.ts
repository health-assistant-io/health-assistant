import { useEffect, RefObject } from 'react';

/**
 * Hook to auto-scroll a container into view when the active tab changes.
 * Useful for long pages where switching tabs might leave the user looking at the bottom of the previous tab's content.
 * 
 * @param ref The ref of the element to scroll to (usually the tab container)
 * @param activeTab The current active tab value
 * @param offset Offset from the top to prevent jumping under fixed headers
 */
export const useTabScroll = (
  ref: RefObject<HTMLElement>, 
  activeTab: string | undefined,
  offset: number = 120
) => {
  useEffect(() => {
    if (ref.current && activeTab) {
      // Small delay to ensure content has started rendering and DOM is updated
      const timer = setTimeout(() => {
        const rect = ref.current?.getBoundingClientRect();
        
        // Only scroll if the element is not already well-positioned in the viewport
        // (e.g. if it's already at top or we are scrolling up to it)
        if (rect && rect.top < offset) {
          // It's already near the top or above (scrolled past), we want it to be at the 'offset' position
          window.scrollTo({
            top: window.scrollY + rect.top - offset,
            behavior: 'smooth'
          });
        } else if (rect && rect.top > window.innerHeight - 200) {
            // It's too far down, bring it up
            ref.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      }, 150);
      
      return () => clearTimeout(timer);
    }
  }, [activeTab, ref, offset]);
};
