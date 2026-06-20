import React, { useState, useEffect, useRef } from 'react';
import { AIChatInterface } from './AIChatInterface';
import { useTranslation } from 'react-i18next';

const STORAGE_KEY = 'ai-drawer-width';
const DEFAULT_WIDTH = 560;
const MIN_WIDTH = 384;
const MAX_WIDTH = 860;
const MOBILE_BREAKPOINT = 640;

const readStoredWidth = (): number => {
  if (typeof window === 'undefined') return DEFAULT_WIDTH;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_WIDTH;
    const val = parseInt(raw, 10);
    if (Number.isNaN(val)) return DEFAULT_WIDTH;
    return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, val));
  } catch {
    return DEFAULT_WIDTH;
  }
};

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

export const AIDrawer: React.FC<Props> = ({ isOpen, onClose }) => {
  const { t } = useTranslation();
  const [drawerWidth, setDrawerWidth] = useState<number>(readStoredWidth);
  const [isMobile, setIsMobile] = useState<boolean>(
    () => typeof window !== 'undefined' && window.innerWidth < MOBILE_BREAKPOINT
  );
  const drawerRef = useRef<HTMLDivElement>(null);

  // Track viewport to keep full-width on mobile
  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth < MOBILE_BREAKPOINT);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const startDrag = (e: React.MouseEvent<HTMLDivElement> | React.TouchEvent<HTMLDivElement>) => {
    if (isMobile || !drawerRef.current) return;
    e.preventDefault();
    e.stopPropagation();

    const clientX = 'touches' in e ? e.touches[0].clientX : e.clientX;
    const startX = clientX;
    const startWidth = drawerRef.current.offsetWidth;

    const el = drawerRef.current;
    document.body.classList.add('select-none');
    document.body.style.cursor = 'col-resize';

    const onMove = (ev: MouseEvent | TouchEvent) => {
      ev.preventDefault();
      const moveX = 'touches' in ev ? ev.touches[0].clientX : (ev as MouseEvent).clientX;
      // Drawer is anchored right: moving pointer left increases width
      const delta = startX - moveX;
      const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth + delta));
      // Mutate DOM directly to avoid re-rendering the heavy chat subtree per frame
      el.style.width = `${next}px`;
    };

    const onUp = () => {
      // Commit final width to React state once, then persist
      const finalWidth = el.offsetWidth;
      setDrawerWidth(finalWidth);
      try {
        window.localStorage.setItem(STORAGE_KEY, String(finalWidth));
      } catch {
        /* ignore */
      }
      document.body.classList.remove('select-none');
      document.body.style.cursor = '';
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      window.removeEventListener('touchmove', onMove);
      window.removeEventListener('touchend', onUp);
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    window.addEventListener('touchmove', onMove, { passive: false });
    window.addEventListener('touchend', onUp);
  };

  if (!isOpen) return null;

  const widthStyle: React.CSSProperties | undefined = isMobile ? undefined : { width: drawerWidth };

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/20 backdrop-blur-[2px] z-[550] animate-in fade-in duration-300"
        onClick={onClose}
      />

      {/* Drawer */}
      <div
        ref={drawerRef}
        className="fixed top-0 right-0 h-screen w-full bg-white dark:bg-dark-bg z-[560] shadow-[-20px_0_50px_rgba(0,0,0,0.1)] border-l border-gray-100 dark:border-dark-border flex flex-col animate-in slide-in-from-right duration-300"
        style={widthStyle}
      >
        <AIChatInterface isFullScreen={false} onClose={onClose} />

        {/* Resize Handle (left edge) */}
        {!isMobile && (
          <div
            onMouseDown={startDrag}
            onTouchStart={startDrag}
            className="group absolute left-0 top-0 h-full w-2 -ml-1 cursor-col-resize z-[570] flex items-center justify-center"
            title={t('ai_chat.tooltips.resize')}
            role="separator"
            aria-orientation="vertical"
            aria-label={t('ai_chat.tooltips.resize')}
            aria-valuenow={drawerWidth}
            aria-valuemin={MIN_WIDTH}
            aria-valuemax={MAX_WIDTH}
          >
            <div className="absolute inset-y-0 left-1 w-1 bg-transparent group-hover:bg-indigo-500/30 group-active:bg-indigo-500/50 transition-colors" />
            <div className="relative w-1 h-10 rounded-full bg-gray-200 dark:bg-dark-border group-hover:bg-indigo-500 group-active:bg-indigo-600 transition-colors" />
          </div>
        )}
      </div>
    </>
  );
};
