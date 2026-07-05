import { Check, Trash2, ChevronRight } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import type { Locale } from 'date-fns';
import type { NotificationInboxItem, RecipientStatus } from '../../services/notificationService';
import { CategoryIcon, SourceBadge, SeverityDot, SEVERITY_STRIPE } from './notificationUi';

const STATUS_LABEL: Record<RecipientStatus, string> = {
  unread: 'Unread',
  read: 'Read',
  dismissed: 'Dismissed',
};

interface Props {
  item: NotificationInboxItem;
  dateLocale: Locale;
  selectMode: boolean;
  selected: boolean;
  onToggleSelect: (id: string) => void;
  onClick: (item: NotificationInboxItem) => void;
  onMarkRead?: (recipientId: string) => void;
  onDismiss: (recipientId: string) => void;
}

export function NotificationItem({
  item,
  dateLocale,
  selectMode,
  selected,
  onToggleSelect,
  onClick,
  onMarkRead,
  onDismiss,
}: Props) {
  const n = item.notification;
  const isUnread = item.status === 'unread';
  const isDismissed = item.status === 'dismissed';
  const hasActions = (n.payload?.actions?.length ?? 0) > 0;

  return (
    <li
      className={`border-l-2 ${SEVERITY_STRIPE[n.severity] ?? SEVERITY_STRIPE.info} ${
        selected ? 'bg-blue-50 dark:bg-blue-900/20' : isUnread ? 'bg-blue-50/40 dark:bg-blue-900/5' : ''
      } ${isDismissed ? 'opacity-60' : ''} hover:bg-gray-50/80 dark:hover:bg-dark-border/30 transition-colors group`}
    >
      <div className="flex items-start gap-2 px-3 py-3">
        {/* Selection checkbox — only visible in select mode */}
        {selectMode && (
          <input
            type="checkbox"
            checked={selected}
            onChange={() => onToggleSelect(item.recipient_id)}
            className="mt-1 shrink-0"
            aria-label={`Select ${n.title}`}
            onClick={(e) => e.stopPropagation()}
          />
        )}

        {/* Category icon */}
        <div className="p-1.5 rounded-lg bg-gray-50 dark:bg-dark-bg shrink-0 mt-0.5">
          <CategoryIcon category={n.category} />
        </div>

        {/* Main content (clickable) */}
        <button
          onClick={() => onClick(item)}
          className="flex-1 min-w-0 text-left"
          title={selectMode ? (selected ? 'Click to deselect' : 'Click to select') : 'Open details'}
        >
          <div className="flex items-center gap-1.5 flex-wrap mb-0.5">
            <SourceBadge source={n.source} />
            <span className="text-[9px] text-gray-400 uppercase font-bold tracking-wider">{n.type.replace(/_/g, ' ')}</span>
            {isUnread && <SeverityDot severity={n.severity} />}
            {n.created_at && (
              <span className="text-[10px] text-gray-400 ml-auto whitespace-nowrap">
                {formatDistanceToNow(new Date(n.created_at), { addSuffix: true, locale: dateLocale })}
              </span>
            )}
          </div>
          <p
            className={`text-sm font-bold truncate ${
              isUnread ? 'text-gray-900 dark:text-dark-text' : 'text-gray-600 dark:text-dark-muted'
            }`}
          >
            {n.title}
          </p>
          {n.body && (
            <p className="text-xs text-gray-500 dark:text-dark-muted line-clamp-1 mt-0.5">{n.body}</p>
          )}
          {hasActions && !selectMode && (
            <p className="text-[10px] text-blue-600 dark:text-blue-400 mt-1 inline-flex items-center gap-0.5 font-semibold">
              actionable <ChevronRight className="w-3 h-3" />
            </p>
          )}
        </button>

        {/* Hover actions — hidden in select mode to keep the UX strictly about selecting */}
        {!selectMode && (
          <div className="flex items-center space-x-0.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
            {isUnread && onMarkRead && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onMarkRead(item.recipient_id);
                }}
                title="Mark read"
                className="p-1.5 text-gray-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-lg"
              >
                <Check className="w-4 h-4" />
              </button>
            )}
            <button
              onClick={(e) => {
                e.stopPropagation();
                onDismiss(item.recipient_id);
              }}
              title="Dismiss"
              className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          </div>
        )}
      </div>
    </li>
  );
}

export { STATUS_LABEL };
