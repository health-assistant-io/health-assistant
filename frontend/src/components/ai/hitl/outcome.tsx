import React, { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Link2, AlertCircle, X } from 'lucide-react';

export interface OutcomeChip {
  icon?: React.ComponentType<{ className?: string }>;
  label: string;
  tone?: 'default' | 'error';
}

export const HitlOutcomeDetail: React.FC<{ chips: OutcomeChip[] }> = ({ chips }) => {
  if (!chips.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5 mt-2 pt-2 border-t border-gray-100 dark:border-dark-border">
      {chips.map((c, i) => {
        const Icon = c.icon;
        const isError = c.tone === 'error';
        return (
          <span
            key={i}
            className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[10px] font-bold ${
              isError
                ? 'bg-rose-50 dark:bg-rose-900/20 border-rose-200 dark:border-rose-500/30 text-rose-600 dark:text-rose-400'
                : 'bg-gray-50 dark:bg-dark-bg border-gray-200 dark:border-dark-border text-gray-600 dark:text-dark-text'
            }`}
          >
            {Icon && (
              <Icon
                className={`w-2.5 h-2.5 ${
                  isError ? 'text-rose-500' : 'text-emerald-500 dark:text-emerald-400'
                }`}
              />
            )}
            <span>{c.label}</span>
          </span>
        );
      })}
    </div>
  );
};

export function buildLinkOutcomeChips(
  result: { links?: unknown; links_failed?: number } | undefined,
): OutcomeChip[] {
  if (!result) return [];
  const links = Array.isArray(result.links)
    ? (result.links as Array<{ ok?: boolean }>)
    : [];
  const okCount = links.filter(l => l.ok).length;
  const failed =
    typeof result.links_failed === 'number'
      ? result.links_failed
      : links.filter(l => !l.ok).length;
  const chips: OutcomeChip[] = [];
  if (okCount) chips.push({ icon: Link2, label: `${okCount} links created` });
  if (failed) chips.push({ icon: AlertCircle, label: `${failed} links failed`, tone: 'error' });
  return chips;
}

export interface OutcomeDetailModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  icon?: React.ComponentType<{ className?: string }>;
  iconClassName?: string;
  maxWidth?: string;
  children: React.ReactNode;
}

export const OutcomeDetailModal: React.FC<OutcomeDetailModalProps> = ({
  isOpen,
  onClose,
  title,
  subtitle,
  icon: Icon,
  iconClassName,
  maxWidth = 'max-w-2xl',
  children,
}) => {
  const { t } = useTranslation();

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-modal flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm animate-in fade-in duration-200"
      onClick={onClose}
    >
      <div
        className={`bg-white dark:bg-dark-surface w-full ${maxWidth} rounded-3xl shadow-2xl border border-gray-100 dark:border-dark-border overflow-hidden flex flex-col max-h-[85vh]`}
        onClick={e => e.stopPropagation()}
      >
        <div className="px-5 py-3 border-b border-gray-50 dark:border-dark-border flex items-center justify-between bg-gradient-to-r from-indigo-50/50 to-white dark:from-indigo-900/10 dark:to-dark-surface shrink-0">
          <div className="flex items-center gap-2.5 min-w-0">
            {Icon && (
              <div className={`p-1.5 rounded-lg bg-indigo-500/10 ${iconClassName ?? 'text-indigo-600 dark:text-indigo-400'}`}>
                <Icon className="w-4 h-4" />
              </div>
            )}
            <div className="min-w-0">
              <h3 className="text-sm font-bold text-gray-900 dark:text-dark-text truncate">{title}</h3>
              {subtitle && (
                <p className="text-[11px] text-gray-500 dark:text-dark-muted truncate">{subtitle}</p>
              )}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors"
            aria-label={t('common.cancel', 'Close')}
          >
            <X className="w-4 h-4 text-gray-400" />
          </button>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto p-4 custom-scrollbar">{children}</div>
      </div>
    </div>,
    document.body
  );
};
