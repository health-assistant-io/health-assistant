import { useState, useRef, useEffect } from 'react';
import { Bell, X, Check, Clock, AlertCircle, Pill, Calendar, MessageSquare, ShieldAlert } from 'lucide-react';
import { useNotificationStore } from '../../store/notificationStore';
import { usePatientStore } from '../../store/slices/patientSlice';
import { formatDistanceToNow } from 'date-fns';
import { el, enUS } from 'date-fns/locale';
import { useTranslation } from 'react-i18next';
import { Notification } from '../../services/notificationService';

export function NotificationBell() {
  const { t, i18n } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const { notifications, unreadCount, fetchNotifications, markAsRead, startPolling, stopPolling } = useNotificationStore();
  const { currentPatient } = usePatientStore();
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (currentPatient?.id) {
      startPolling(currentPatient.id);
    }
    return () => stopPolling();
  }, [currentPatient?.id]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const getIcon = (type: string) => {
    switch (type) {
      case 'medication_reminder': return <Pill className="w-4 h-4 text-blue-500" />;
      case 'examination_reminder': return <Calendar className="w-4 h-4 text-green-500" />;
      case 'biomarker_alert': return <ShieldAlert className="w-4 h-4 text-red-500" />;
      case 'ai_suggestion': return <MessageSquare className="w-4 h-4 text-indigo-500" />;
      default: return <Bell className="w-4 h-4 text-gray-400" />;
    }
  };

  const dateLocale = i18n.language === 'el' ? el : enUS;

  return (
    <div className="relative" ref={menuRef}>
      <button 
        onClick={() => setIsOpen(!isOpen)}
        className="relative p-2 text-gray-400 hover:text-gray-500 dark:text-dark-muted dark:hover:text-dark-text transition-colors rounded-full hover:bg-gray-100 dark:hover:bg-dark-border"
      >
        <Bell className="h-5 w-5" />
        {unreadCount > 0 && (
          <span className="absolute top-1 right-1 flex h-4 w-4 items-center justify-center rounded-full bg-red-500 text-[10px] font-bold text-white ring-2 ring-white dark:ring-dark-surface">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      {isOpen && (
        <div className="absolute right-0 mt-3 w-80 sm:w-96 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-xl shadow-xl z-[550] overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200">
          <div className="px-4 py-3 border-b border-gray-50 dark:border-dark-border flex items-center justify-between bg-gray-50/50 dark:bg-dark-bg/50">
            <h3 className="text-sm font-bold text-gray-700 dark:text-dark-text">{t('common.notifications')}</h3>
            <span className="text-[10px] font-medium px-2 py-0.5 bg-blue-50 text-blue-600 rounded-full dark:bg-blue-900/20 dark:text-blue-400">
              {unreadCount} {t('common.unread')}
            </span>
          </div>

          <div className="max-h-[400px] overflow-y-auto">
            {notifications.length === 0 ? (
              <div className="px-4 py-8 text-center">
                <Bell className="w-12 h-12 text-gray-200 dark:text-dark-border mx-auto mb-3" />
                <p className="text-sm text-gray-400 dark:text-dark-muted">{t('common.no_notifications')}</p>
              </div>
            ) : (
              <div className="divide-y divide-gray-50 dark:divide-dark-border">
                {notifications.map((notif) => {
                  const isUnread = notif.status === 'pending' || notif.status === 'delivered';
                  return (
                    <div 
                      key={notif.id} 
                      className={`px-4 py-3 hover:bg-gray-50 dark:hover:bg-dark-border transition-colors cursor-pointer group relative ${isUnread ? 'bg-blue-50/30 dark:bg-blue-900/5' : ''}`}
                      onClick={() => isUnread && markAsRead(notif.id)}
                    >
                      <div className="flex space-x-3">
                        <div className={`mt-0.5 flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center ${isUnread ? 'bg-white dark:bg-dark-bg shadow-sm' : 'bg-gray-50 dark:bg-dark-bg'}`}>
                          {getIcon(notif.type)}
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-start justify-between">
                            <p className={`text-sm font-semibold truncate ${isUnread ? 'text-gray-900 dark:text-dark-text' : 'text-gray-500 dark:text-dark-muted'}`}>
                              {notif.title}
                            </p>
                            <span className="text-[10px] text-gray-400 dark:text-dark-muted whitespace-nowrap ml-2">
                              {formatDistanceToNow(new Date(notif.created_at), { addSuffix: true, locale: dateLocale })}
                            </span>
                          </div>
                          <p className={`text-xs mt-0.5 line-clamp-2 ${isUnread ? 'text-gray-600 dark:text-dark-muted' : 'text-gray-400'}`}>
                            {notif.body}
                          </p>
                        </div>
                      </div>
                      {isUnread && (
                        <div className="absolute top-3 right-4 opacity-0 group-hover:opacity-100 transition-opacity">
                          <button 
                            onClick={(e) => {
                              e.stopPropagation();
                              markAsRead(notif.id);
                            }}
                            className="p-1 bg-white dark:bg-dark-bg rounded-md shadow-sm border border-gray-100 dark:border-dark-border text-blue-500 hover:text-blue-600"
                          >
                            <Check className="w-3 h-3" />
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {notifications.length > 0 && (
            <div className="px-4 py-2 border-t border-gray-50 dark:border-dark-border bg-gray-50/50 dark:bg-dark-bg/50 text-center">
              <button className="text-[11px] font-bold text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300">
                {t('common.view_all_notifications')}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
