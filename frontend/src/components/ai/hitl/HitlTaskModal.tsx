import React, { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { X, Bot } from 'lucide-react';
import { TaskInfo } from '../../../types/ai';
import { HitlTaskHandler, HITL_STATUS_META } from './registry';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  task: TaskInfo;
  sessionId: string | null;
  handler: HitlTaskHandler;
  onResolved: (updated: TaskInfo) => void;
}

const TONE_CHIP: Record<string, string> = {
  amber: 'bg-amber-50 dark:bg-amber-900/30 text-amber-600 dark:text-amber-300',
  emerald: 'bg-emerald-50 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-300',
  rose: 'bg-rose-50 dark:bg-rose-900/30 text-rose-600 dark:text-rose-300',
  gray: 'bg-gray-100 dark:bg-dark-bg text-gray-500 dark:text-dark-muted',
};

/**
 * Generic popup shell for a human-in-the-loop task. Renders a uniform header
 * (AI Proposal + task title + status + close) and hosts the handler's full
 * interactive form as the body. Closes on Escape, backdrop click, or when the
 * form resolves (confirm/dismiss).
 */
export const HitlTaskModal: React.FC<Props> = ({ isOpen, onClose, task, sessionId, handler, onResolved }) => {
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

  const statusMeta = HITL_STATUS_META[task.status] ?? HITL_STATUS_META.proposed;
  const StatusIcon = statusMeta.icon;
  const HandlerIcon = handler.icon;
  const FormComponent = handler.FormComponent;

  // Wrap onResolved so approving/rejecting from inside the form also closes the modal.
  const handleResolved = (updated: TaskInfo) => {
    onResolved(updated);
    onClose();
  };

  // Cancel = close the modal with no state change (proposal stays pending).
  const handleCancel = () => {
    onClose();
  };

  return createPortal(
    <div
      className="fixed inset-0 z-[1100] flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm animate-in fade-in duration-200"
      onClick={onClose}
    >
      <div
        className="bg-white dark:bg-dark-surface w-full max-w-4xl rounded-3xl shadow-2xl border border-gray-100 dark:border-dark-border overflow-hidden flex flex-col max-h-[90vh]"
        onClick={e => e.stopPropagation()}
      >
        {/* Uniform HITL header */}
        <div className="px-6 py-4 border-b border-gray-50 dark:border-dark-border flex items-center justify-between bg-gradient-to-r from-indigo-50/50 to-white dark:from-indigo-900/10 dark:to-dark-surface shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <div className="p-2 rounded-xl bg-indigo-500/10 text-indigo-600 dark:text-indigo-400">
              <HandlerIcon className="w-4 h-4" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-1.5">
                <Bot className="w-3 h-3 text-gray-400 dark:text-dark-muted" />
                <span className="text-[9px] font-black uppercase tracking-[0.2em] text-gray-400 dark:text-dark-muted">
                  {t('ai_chat.hitl.proposal_label', 'AI Proposal')}
                </span>
              </div>
              <h2 className="text-base font-bold text-gray-900 dark:text-dark-text tracking-tight truncate">
                {task.title || t('ai_chat.hitl.default_title', 'Review proposed action')}
              </h2>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <div
              className={`flex items-center gap-1 px-2 py-1 rounded-full text-[9px] font-black uppercase tracking-widest ${
                TONE_CHIP[statusMeta.tone] ?? TONE_CHIP.amber
              }`}
            >
              <StatusIcon className="w-3 h-3" />
              <span>{t(statusMeta.labelKey)}</span>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors"
              aria-label={t('common.cancel', 'Close')}
            >
              <X className="w-5 h-5 text-gray-400" />
            </button>
          </div>
        </div>

        {/* Body: handler's full form (owns approve/cancel/reject actions) */}
        <FormComponent task={task} sessionId={sessionId} onResolved={handleResolved} onCancel={handleCancel} />
      </div>
    </div>,
    document.body
  );
};
