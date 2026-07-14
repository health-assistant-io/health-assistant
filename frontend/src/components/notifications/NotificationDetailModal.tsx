import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { X, ExternalLink, User, Clock, Activity, FileText, Tag } from 'lucide-react';
import { format } from 'date-fns';
import type { Locale } from 'date-fns';
import type { NotificationInboxItem, NotificationAction } from '../../services/notificationService';
import axios from '../../api/axios';
import DisplayBlockRenderer from '../integrations/displayBlocks';
import { CategoryIcon, SourceBadge, SeverityDot, actionHandlerFor, actionClassName } from './notificationUi';
import { useModalA11y } from '../../hooks/useModalA11y';

interface Props {
  item: NotificationInboxItem;
  dateLocale: Locale;
  onClose: () => void;
  onMarkRead?: (recipientId: string) => void;
}

export function NotificationDetailModal({ item, dateLocale, onClose, onMarkRead }: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const n = item.notification;
  const [actionResult, setActionResult] = useState<{ message: string; results?: any[] } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState<string | null>(null); // action.id while in flight

  useModalA11y(true, onClose);

  const handlePost = async (action: NotificationAction) => {
    if (!action.endpoint) return;
    setActionBusy(action.id);
    setActionError(null);
    setActionResult(null);
    try {
      const r = await axios.post(action.endpoint, {});
      setActionResult(r.data);
    } catch (err: any) {
      setActionError(
        err?.response?.data?.detail ?? err?.message ?? 'Action failed'
      );
    } finally {
      setActionBusy(null);
    }
  };

  const actions: NotificationAction[] = n.payload?.actions ?? [];
  const displayBlocks: any[] = n.payload?.display_blocks ?? [];

  return (
    <div className="fixed inset-0 z-modal flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        role="dialog" aria-modal="true"
        className="bg-white dark:bg-dark-surface rounded-2xl shadow-xl border border-gray-200 dark:border-dark-border w-full max-w-2xl max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between p-5 border-b border-gray-100 dark:border-dark-border">
          <div className="flex items-start gap-3 min-w-0 flex-1">
            <div className="p-2 rounded-xl bg-gray-50 dark:bg-dark-bg shrink-0">
              <CategoryIcon category={n.category} className="w-5 h-5" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5 flex-wrap mb-1">
                <SourceBadge source={n.source} />
                <span className="text-[10px] text-gray-400 uppercase font-bold tracking-wider">{n.category}</span>
                <SeverityDot severity={n.severity} />
                <span className="text-[10px] text-gray-400 uppercase">{n.severity}</span>
              </div>
              <h3 className="text-base font-bold text-gray-900 dark:text-dark-text break-words">{n.title}</h3>
              {n.created_at && (
                <p className="text-xs text-gray-400 mt-1 flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  {format(new Date(n.created_at), 'PPpp', { locale: dateLocale })}
                </p>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-100 dark:hover:bg-dark-border rounded-lg shrink-0"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {n.body && (
            <div>
              <p className="text-[10px] text-gray-400 uppercase font-bold tracking-wider mb-1.5 flex items-center gap-1">
                <FileText className="w-3 h-3" /> Message
              </p>
              <p className="text-sm text-gray-700 dark:text-dark-muted whitespace-pre-wrap leading-relaxed">{n.body}</p>
            </div>
          )}

          {/* Meta grid */}
          <div className="grid grid-cols-2 gap-3 text-xs">
            <MetaRow icon={<Tag className="w-3 h-3" />} label="Type" value={n.type} />
            <MetaRow
              icon={<Activity className="w-3 h-3" />}
              label="Status"
              value={item.status}
            />
            {n.patient_id && (
              <MetaRow
                icon={<User className="w-3 h-3" />}
                label="Patient"
                value={
                  <button
                    onClick={() => navigate(`/patients/${n.patient_id}`)}
                    className="text-blue-600 dark:text-blue-400 hover:underline font-mono text-[11px]"
                  >
                    {n.patient_id.slice(0, 8)}…
                  </button>
                }
              />
            )}
            {n.sender_user_id && (
              <MetaRow
                icon={<User className="w-3 h-3" />}
                label="Sender"
                value={<span className="font-mono text-[11px]">{n.sender_user_id.slice(0, 8)}…</span>}
              />
            )}
          </div>

          {/* HITL / source_ref / payload details */}
          {(n.source_ref || n.payload) && (
            <details className="border border-gray-100 dark:border-dark-border rounded-xl">
              <summary className="cursor-pointer px-3 py-2 text-xs font-bold text-gray-500 dark:text-dark-muted select-none">
                Raw metadata (source_ref + payload)
              </summary>
              <div className="px-3 pb-3 space-y-2">
                {n.source_ref && (
                  <pre className="text-[10px] bg-gray-50 dark:bg-dark-bg rounded p-2 overflow-x-auto text-gray-600 dark:text-dark-muted">
                    {JSON.stringify(n.source_ref, null, 2)}
                  </pre>
                )}
                {n.payload && (
                  <pre className="text-[10px] bg-gray-50 dark:bg-dark-bg rounded p-2 overflow-x-auto text-gray-600 dark:text-dark-muted">
                    {JSON.stringify({ ...n.payload, actions: undefined, display_blocks: undefined }, null, 2)}
                  </pre>
                )}
              </div>
            </details>
          )}

          {/* Structured display blocks (integration-authored tables/lists/etc.) */}
          {displayBlocks.length > 0 && (
            <div className="space-y-3">
              {displayBlocks.map((block, idx) => (
                <div key={idx}>
                  {block.title && (
                    <p className="text-[10px] text-gray-400 uppercase font-bold tracking-wider mb-1.5">
                      {block.title}
                    </p>
                  )}
                  <DisplayBlockRenderer block={block} />
                </div>
              ))}
            </div>
          )}

          {/* Action button result / error */}
          {actionError && (
            <p className="text-xs text-red-600 bg-red-50 dark:bg-red-900/20 rounded-lg px-3 py-2">
              {actionError}
            </p>
          )}
          {actionResult && (
            <div className="text-xs bg-emerald-50 dark:bg-emerald-900/20 rounded-lg p-3 space-y-2">
              {actionResult.message && (
                <p className="text-emerald-700 dark:text-emerald-400 font-semibold">
                  {actionResult.message}
                </p>
              )}
              {actionResult.results?.map((block, idx) => (
                <div key={idx}>
                  {block.title && (
                    <p className="text-[10px] text-gray-500 dark:text-dark-muted uppercase font-bold tracking-wider mb-1">
                      {block.title}
                    </p>
                  )}
                  <DisplayBlockRenderer block={block} />
                </div>
              ))}
            </div>
          )}

          {/* Action buttons */}
          {actions.length > 0 && (
            <div className="flex flex-wrap gap-2 pt-2 border-t border-gray-100 dark:border-dark-border">
              {actions.map((action) => (
                <button
                  key={action.id}
                  onClick={() => actionHandlerFor(action, { navigate, onPost: handlePost })()}
                  disabled={actionBusy !== null}
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${actionClassName(action.style)}`}
                >
                  {actionBusy === action.id && <Clock className="w-3 h-3 animate-spin" />}
                  {action.type === 'link' && <ExternalLink className="w-3 h-3" />}
                  {action.label}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-2 p-4 border-t border-gray-100 dark:border-dark-border bg-gray-50/50 dark:bg-dark-bg/40 rounded-b-2xl">
          <span className="text-[10px] text-gray-400 uppercase font-bold tracking-wider">
            recipient {item.recipient_id.slice(0, 8)}…
          </span>
          <div className="flex gap-2">
            {item.status === 'unread' && onMarkRead && (
              <button
                onClick={() => {
                  onMarkRead(item.recipient_id);
                  onClose();
                }}
                className="px-3 py-1.5 text-xs font-semibold text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-lg"
              >
                {t('common.mark_read', { defaultValue: 'Mark read' })}
              </button>
            )}
            <button
              onClick={onClose}
              className="px-3 py-1.5 text-xs font-semibold text-gray-600 dark:text-dark-muted hover:bg-gray-100 dark:hover:bg-dark-border rounded-lg"
            >
              {t('common.close', { defaultValue: 'Close' })}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function MetaRow({ icon, label, value }: { icon: React.ReactNode; label: string; value: React.ReactNode }) {
  return (
    <div>
      <p className="text-gray-400 uppercase font-bold tracking-wider text-[10px] mb-0.5 flex items-center gap-1">
        {icon} {label}
      </p>
      <div className="text-gray-900 dark:text-dark-text font-semibold capitalize">{value}</div>
    </div>
  );
}
