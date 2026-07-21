import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Bot, Pencil, X } from 'lucide-react';
import { TaskInfo } from '../../../types/ai';
import { getHitlHandler, HITL_STATUS_META } from './registry';
import { HitlTaskModal } from './HitlTaskModal';
import { resolveHitlTask } from '../../../services/aiAssistanceService';
import { AIBadge } from '../../ui/AIBadge';

interface Props {
  task: TaskInfo;
  sessionId: string | null;
  onResolved: (updated: TaskInfo) => void;
}

const TONE_CLASSES: Record<string, { ring: string; chip: string; icon: string }> = {
  indigo: { ring: 'border-indigo-200 dark:border-indigo-500/30', chip: 'bg-indigo-50 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-300', icon: 'text-indigo-500' },
  blue: { ring: 'border-blue-200 dark:border-blue-500/30', chip: 'bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-300', icon: 'text-blue-500' },
  emerald: { ring: 'border-emerald-200 dark:border-emerald-500/30', chip: 'bg-emerald-50 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-300', icon: 'text-emerald-500' },
  amber: { ring: 'border-amber-200 dark:border-amber-500/30', chip: 'bg-amber-50 dark:bg-amber-900/30 text-amber-600 dark:text-amber-300', icon: 'text-amber-500' },
  rose: { ring: 'border-rose-200 dark:border-rose-500/30', chip: 'bg-rose-50 dark:bg-rose-900/30 text-rose-600 dark:text-rose-300', icon: 'text-rose-500' },
  gray: { ring: 'border-gray-200 dark:border-dark-border', chip: 'bg-gray-100 dark:bg-dark-bg text-gray-500 dark:text-dark-muted', icon: 'text-gray-400' },
};

export const HitlTaskCard: React.FC<Props> = ({ task, sessionId, onResolved }) => {
  const { t } = useTranslation();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const handler = getHitlHandler(task.task_type);
  const statusMeta = HITL_STATUS_META[task.status] ?? HITL_STATUS_META.proposed;
  const isProposed = task.status === 'proposed';
  const isInline = handler?.inline === true;

  // Unknown task type — render a minimal fallback so an older/unknown proposal
  // never breaks the chat surface.
  if (!handler) {
    return (
      <div className="mt-4 rounded-2xl border border-amber-200 dark:border-amber-500/30 bg-amber-50/60 dark:bg-amber-900/10 p-4 text-xs text-amber-700 dark:text-amber-300">
        <div className="flex items-center gap-2">
          <Bot className="w-3.5 h-3.5" />
          <span className="font-black uppercase tracking-widest">
            {t('ai_chat.hitl.unknown_title', 'Unsupported action')}
          </span>
        </div>
        <p className="mt-1 opacity-80">
          {t('ai_chat.hitl.unknown_desc', {
            defaultValue: 'An action of type "{{type}}" was proposed but is not supported in this view.',
            type: task.task_type,
          })}
        </p>
      </div>
    );
  }

  const accent = TONE_CLASSES[handler.accent] ?? TONE_CLASSES.indigo;
  const statusTone = TONE_CLASSES[statusMeta.tone] ?? TONE_CLASSES.amber;
  const StatusIcon = statusMeta.icon;
  const HandlerIcon = handler.icon;
  // Extract the form component to a local that does not collide with the
  // `FormComponent` registry field name (kept for parity with the modal path).
  const FormComponentInternal = handler.FormComponent;

  // Resolved cards collapse to a compact summary.
  if (!isProposed) {
    const tone = TONE_CLASSES[statusMeta.tone] ?? TONE_CLASSES.gray;
    const label = task.proposed_payload?.title || task.title || task.task_type;
    return (
      <div className={`mt-4 rounded-2xl border ${tone.ring} bg-white dark:bg-dark-surface p-3 flex items-center gap-3`}>
        <StatusIcon className={`w-4 h-4 flex-shrink-0 ${tone.icon}`} />
        <div className="min-w-0 flex-1">
          <div className="text-[9px] font-black uppercase tracking-[0.2em] opacity-60">
            {t(statusMeta.labelKey)}
          </div>
          <div className="text-xs font-bold text-gray-800 dark:text-dark-text truncate">{label}</div>
        </div>
      </div>
    );
  }

  const handleReject = () => {
    if (sessionId) {
      // Best-effort reject record; backend marks the task dismissed.
      resolveHitlTask(sessionId, task.proposal_id, { status: 'dismissed' }).catch(err =>
        console.error('HITL reject record failed', err)
      );
    }
    onResolved({ ...task, status: 'dismissed', resolved: { at: new Date().toISOString() } });
  };

  return (
    <>
      <div className={`hitl-card mt-4 w-full min-w-0 rounded-2xl border ${accent.ring} bg-white dark:bg-dark-surface shadow-lg overflow-hidden transition-shadow hover:shadow-xl`}>
        {/* Header — base layout is a column (title full-width, status below)
            so the title is never squeezed by the status pill; a container
            query restores the inline row when the card is wide enough. */}
        <div className="hitl-header px-4 py-3 border-b border-gray-50 dark:border-dark-border bg-gradient-to-r from-gray-50/60 to-white dark:from-dark-bg/40 dark:to-dark-surface">
          <div className="hitl-title-row flex items-center gap-2.5 min-w-0">
            <div className={`p-1.5 rounded-lg ${accent.chip} flex-shrink-0`}>
              <HandlerIcon className="w-3.5 h-3.5" />
            </div>
            <div className="min-w-0 flex-1">
              <AIBadge size="sm" label={t('ai_chat.hitl.proposal_label', 'Proposal')} workflow="chat" />
              <div className="text-sm font-bold text-gray-800 dark:text-dark-text break-words leading-snug">
                {task.title || t('ai_chat.hitl.default_title', 'Review proposed action')}
              </div>
            </div>
          </div>
          <div className={`hitl-status flex items-center gap-1 px-2 py-1 rounded-full text-[9px] font-black uppercase tracking-widest ${statusTone.chip}`}>
            <StatusIcon className="w-3 h-3" />
            <span>{t(statusMeta.labelKey)}</span>
          </div>
        </div>

        {/* Body: compact summary + actions */}
        <div className="px-4 py-3 space-y-3 min-w-0">
          {/* Inline handlers render their form directly in the card body — no
              modal, no "Review & Edit" button. The form owns its full footer
              (Submit/Skip). The hint is hidden because the form's own labels
              carry the prompts. */}
          {isInline ? (
            <div className="min-w-0">
              <FormComponentInternal
                task={task}
                sessionId={sessionId}
                onResolved={onResolved}
                onCancel={() => setIsModalOpen(false)}
              />
            </div>
          ) : (
            <>
              <div className="hitl-hint text-[11px] text-gray-500 dark:text-dark-muted leading-relaxed">
                {t('ai_chat.hitl.review_hint', {
                  defaultValue: 'Review the AI-prepared draft and edit before saving.',
                })}
              </div>
              <div className="min-w-0">{handler.renderSummary(task)}</div>

              <div className="hitl-actions flex items-center gap-2 pt-1">
                <button
                  type="button"
                  onClick={handleReject}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-[11px] font-bold text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-900/20 transition-colors"
                >
                  <X className="w-3.5 h-3.5" />
                  <span>{t('ai_chat.hitl.reject', 'Reject')}</span>
                </button>
                <button
                  type="button"
                  onClick={() => setIsModalOpen(true)}
                  className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-xl text-[11px] font-black text-white bg-indigo-600 hover:bg-indigo-700 shadow-lg shadow-indigo-500/20 transition-all active:scale-95"
                >
                  <Pencil className="w-3.5 h-3.5" />
                  <span>{t('ai_chat.hitl.review_and_edit', 'Review & Edit')}</span>
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      {!isInline && (
        <HitlTaskModal
          isOpen={isModalOpen}
          onClose={() => setIsModalOpen(false)}
          task={task}
          sessionId={sessionId}
          handler={handler}
          onResolved={onResolved}
        />
      )}
    </>
  );
};
