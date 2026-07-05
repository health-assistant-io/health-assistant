import { useState, useRef, useEffect } from 'react';
import {
  Bell,
  Check,
  Pill,
  MessageSquare,
  ShieldAlert,
  Activity,
  Bot,
  RefreshCw,
  Plug,
  Info,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useNotificationStore } from '../../store/notificationStore';
import { formatDistanceToNow } from 'date-fns';
import { el, enUS } from 'date-fns/locale';
import { useTranslation } from 'react-i18next';
import type { NotificationCategory, NotificationEvent, NotificationSeverity, NotificationAction, NotificationInboxItem } from '../../services/notificationService';
import { useNotificationStream } from '../../hooks/useNotificationStream';
import { NotificationDetailModal } from '../notifications/NotificationDetailModal';

const SEVERITY_DOT: Record<NotificationSeverity, string> = {
  info: 'bg-blue-500',
  warning: 'bg-amber-500',
  critical: 'bg-red-500',
};

function CategoryIcon({ category }: { category: NotificationCategory }) {
  switch (category) {
    case 'reminder':
      return <Pill className="w-4 h-4 text-blue-500" />;
    case 'alert':
      return <ShieldAlert className="w-4 h-4 text-red-500" />;
    case 'hitl':
      return <MessageSquare className="w-4 h-4 text-indigo-500" />;
    case 'agent':
      return <Bot className="w-4 h-4 text-purple-500" />;
    case 'system':
      return <Info className="w-4 h-4 text-gray-500" />;
    case 'integration':
      return <Plug className="w-4 h-4 text-teal-500" />;
    case 'clinical_event':
      return <Activity className="w-4 h-4 text-green-500" />;
    default:
      return <Bell className="w-4 h-4 text-gray-400" />;
  }
}

export function NotificationBell() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [isOpen, setIsOpen] = useState(false);
  const [detail, setDetail] = useState<NotificationInboxItem | null>(null);
  const {
    inbox,
    unreadCount,
    connected,
    fetchInbox,
    markRead,
    markAllRead,
  } = useNotificationStore();
  const menuRef = useRef<HTMLDivElement>(null);

  // Real-time stream (user-scoped — works without a patient context).
  useNotificationStream();

  useEffect(() => {
    fetchInbox();
  }, [fetchInbox]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const dateLocale = i18n.language === 'el' ? el : enUS;

  const handleAction = (event: NotificationEvent, url: string) => {
    setIsOpen(false);
    navigate(url);
    // Find the matching inbox item to mark read.
    const item = inbox.find((i) => i.notification.id === event.id);
    if (item && item.status === 'unread') markRead(item.recipient_id);
  };

  // Click on a row in the dropdown: open the detail modal (same surface as
  // the Notification Center), close the dropdown, auto-mark unread → read.
  // Mirrors the NotificationManagement.openDetail pattern.
  const openDetail = (item: NotificationInboxItem) => {
    setIsOpen(false);
    if (item.status === 'unread') {
      setDetail({
        ...item,
        status: 'read',
        read_at: new Date().toISOString(),
      });
      markRead(item.recipient_id);
    } else {
      setDetail(item);
    }
  };

  return (
    <div className="relative" ref={menuRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="relative p-2 text-gray-400 hover:text-gray-500 dark:text-dark-muted dark:hover:text-dark-text transition-colors rounded-full hover:bg-gray-100 dark:hover:bg-dark-border"
        title={connected ? t('common.notifications') : 'Reconnecting…'}
      >
        <Bell className="h-5 w-5" />
        {unreadCount > 0 && (
          <span className="absolute top-1 right-1 flex h-4 w-4 items-center justify-center rounded-full bg-red-500 text-[10px] font-bold text-white ring-2 ring-white dark:ring-dark-surface">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
        {!connected && (
          <span className="absolute bottom-1 right-1 w-2 h-2 rounded-full bg-amber-400 ring-1 ring-white dark:ring-dark-surface" />
        )}
      </button>

      {isOpen && (
        <div className="absolute right-0 mt-3 w-80 sm:w-96 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-xl shadow-xl z-[550] overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200">
          <div className="px-4 py-3 border-b border-gray-50 dark:border-dark-border flex items-center justify-between bg-gray-50/50 dark:bg-dark-bg/50">
            <h3 className="text-sm font-bold text-gray-700 dark:text-dark-text">
              {t('common.notifications')}
            </h3>
            <div className="flex items-center space-x-2">
              {unreadCount > 0 && (
                <button
                  onClick={() => markAllRead()}
                  className="text-[10px] font-bold text-blue-600 hover:text-blue-700 dark:text-blue-400 uppercase tracking-wide flex items-center"
                >
                  <Check className="w-3 h-3 mr-1" />
                  {t('common.mark_all_read', { defaultValue: 'Mark all read' })}
                </button>
              )}
              <span className="text-[10px] font-medium px-2 py-0.5 bg-blue-50 text-blue-600 rounded-full dark:bg-blue-900/20 dark:text-blue-400">
                {unreadCount} {t('common.unread', { defaultValue: 'unread' })}
              </span>
            </div>
          </div>

          <div className="max-h-[400px] overflow-y-auto">
            {inbox.length === 0 ? (
              <div className="px-4 py-8 text-center">
                <Bell className="w-12 h-12 text-gray-200 dark:text-dark-border mx-auto mb-3" />
                <p className="text-sm text-gray-400 dark:text-dark-muted">
                  {t('common.no_notifications', { defaultValue: 'No notifications' })}
                </p>
              </div>
            ) : (
              <div className="divide-y divide-gray-50 dark:divide-dark-border">
                {inbox.slice(0, 20).map((item) => {
                  const notif = item.notification;
                  const isUnread = item.status === 'unread';
                  const actions: NotificationAction[] = notif.payload?.actions ?? [];
                  return (
                    <div
                      key={item.recipient_id}
                      onClick={() => openDetail(item)}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          openDetail(item);
                        }
                      }}
                      className={`px-4 py-3 hover:bg-gray-50 dark:hover:bg-dark-border dark:hover:bg-dark-border/50 transition-colors group relative cursor-pointer ${
                        isUnread ? 'bg-blue-50/30 dark:bg-blue-900/5' : ''
                      }`}
                    >
                      <div className="flex space-x-3">
                        <div
                          className={`mt-0.5 flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center ${
                            isUnread ? 'bg-white dark:bg-dark-bg shadow-sm' : 'bg-gray-50 dark:bg-dark-bg'
                          }`}
                        >
                          <CategoryIcon category={notif.category} />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-start justify-between">
                            <p
                              className={`text-sm font-semibold truncate flex items-center gap-1.5 ${
                                isUnread ? 'text-gray-900 dark:text-dark-text' : 'text-gray-500 dark:text-dark-muted'
                              }`}
                            >
                              <span className={`w-1.5 h-1.5 rounded-full ${SEVERITY_DOT[notif.severity]}`} />
                              {notif.title}
                            </p>
                            <span className="text-[10px] text-gray-400 dark:text-dark-muted whitespace-nowrap ml-2">
                              {notif.created_at
                                ? formatDistanceToNow(new Date(notif.created_at), {
                                    addSuffix: true,
                                    locale: dateLocale,
                                  })
                                : ''}
                            </span>
                          </div>
                          {notif.body && (
                            <p
                              className={`text-xs mt-0.5 line-clamp-2 ${
                                isUnread ? 'text-gray-600 dark:text-dark-muted' : 'text-gray-400'
                              }`}
                            >
                              {notif.body}
                            </p>
                          )}

                          {actions.length > 0 && (
                            <div className="flex flex-wrap gap-1.5 mt-2">
                              {actions.map((action) => (
                                <button
                                  key={action.id}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    if (action.type === 'link' && action.url) {
                                      handleAction(notif, action.url);
                                    }
                                  }}
                                  className={`text-[11px] font-semibold px-2 py-1 rounded-md transition-colors ${
                                    action.style === 'primary'
                                      ? 'bg-blue-600 text-white hover:bg-blue-700'
                                      : 'bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-dark-border dark:text-dark-text'
                                  }`}
                                >
                                  {action.label}
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                      {isUnread && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            markRead(item.recipient_id);
                          }}
                          title={t('common.mark_read', { defaultValue: 'Mark read' })}
                          className="absolute top-3 right-3 p-1 bg-white dark:bg-dark-bg rounded-md shadow-sm border border-gray-100 dark:border-dark-border text-blue-500 hover:text-blue-600 opacity-0 group-hover:opacity-100 transition-opacity"
                        >
                          <Check className="w-3 h-3" />
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <div className="px-4 py-2 border-t border-gray-50 dark:border-dark-border bg-gray-50/50 dark:bg-dark-bg/50 flex items-center justify-between">
            <a
              href="/notifications"
              onClick={(e) => {
                e.preventDefault();
                setIsOpen(false);
                navigate('/notifications');
              }}
              className="text-[11px] font-bold text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
            >
              {t('common.view_all_notifications', { defaultValue: 'View all notifications' })}
            </a>
            <button
              onClick={() => fetchInbox()}
              className="text-gray-400 hover:text-blue-600 transition-colors"
              title={t('common.refresh', { defaultValue: 'Refresh' })}
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}

      {detail && (
        <NotificationDetailModal
          item={detail}
          dateLocale={dateLocale}
          onClose={() => setDetail(null)}
          onMarkRead={markRead}
        />
      )}
    </div>
  );
}
