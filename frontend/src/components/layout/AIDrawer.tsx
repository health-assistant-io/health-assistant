import React, { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChatDrawer } from '../ui/chat';
import { AIChatInterface } from './AIChatInterface';

const STORAGE_KEY = 'ai-drawer-width';
const DEFAULT_WIDTH = 560;
const MIN_WIDTH = 384;
const MAX_WIDTH = 860;

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

/**
 * The AI drawer on the family `ChatDrawer` (Radix dialog: focus trap,
 * Escape, overlay click, drag + keyboard resize). The width stays
 * controlled app-side under the historical ``ai-drawer-width`` storage key
 * so existing users keep their preferred width across the swap.
 */
export const AIDrawer: React.FC<Props> = ({ isOpen, onClose }) => {
  const { t } = useTranslation();
  const [drawerWidth, setDrawerWidth] = useState<number>(readStoredWidth);

  const handleWidthChange = useCallback((width: number) => {
    setDrawerWidth(width);
    try {
      window.localStorage.setItem(STORAGE_KEY, String(width));
    } catch {
      /* ignore */
    }
  }, []);

  return (
    <ChatDrawer
      open={isOpen}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      panel={<AIChatInterface isFullScreen={false} />}
      width={drawerWidth}
      onWidthChange={handleWidthChange}
      minWidth={MIN_WIDTH}
      maxWidth={MAX_WIDTH}
      title={t('ai_chat.header.title')}
      closeLabel={t('ai_chat.tooltips.close')}
      resizeLabel={t('ai_chat.tooltips.resize')}
    />
  );
};
