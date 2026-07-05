import {
  Bell,
  Pill,
  MessageSquare,
  ShieldAlert,
  Activity,
  Bot,
  Plug,
  Info,
} from 'lucide-react';
import type {
  NotificationCategory,
  NotificationSeverity,
  NotificationSource,
  NotificationAction,
} from '../../services/notificationService';

export const SOURCE_COLORS: Record<NotificationSource, string> = {
  SYSTEM: 'bg-gray-100 text-gray-700 dark:bg-dark-border dark:text-dark-text',
  INTEGRATION: 'bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-400',
  AGENT: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
  RULE: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  CLINICAL: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  SCHEDULED: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
};

export const SEVERITY_STRIPE: Record<NotificationSeverity, string> = {
  info: 'border-l-blue-400',
  warning: 'border-l-amber-400',
  critical: 'border-l-red-500',
};

export const SEVERITY_DOT: Record<NotificationSeverity, string> = {
  info: 'bg-blue-500',
  warning: 'bg-amber-500',
  critical: 'bg-red-500',
};

export function CategoryIcon({ category, className = 'w-4 h-4' }: { category: NotificationCategory; className?: string }) {
  switch (category) {
    case 'reminder':
      return <Pill className={`${className} text-blue-500`} />;
    case 'alert':
      return <ShieldAlert className={`${className} text-red-500`} />;
    case 'hitl':
      return <MessageSquare className={`${className} text-indigo-500`} />;
    case 'agent':
      return <Bot className={`${className} text-purple-500`} />;
    case 'system':
      return <Info className={`${className} text-gray-500`} />;
    case 'integration':
      return <Plug className={`${className} text-teal-500`} />;
    case 'clinical_event':
      return <Activity className={`${className} text-green-500`} />;
    default:
      return <Bell className={`${className} text-gray-400`} />;
  }
}

export function SourceBadge({ source }: { source: NotificationSource }) {
  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 text-[9px] font-bold rounded uppercase tracking-wider ${SOURCE_COLORS[source] ?? 'bg-gray-100 text-gray-600'}`}
      title={`Source: ${source}`}
    >
      {source}
    </span>
  );
}

export function SeverityDot({ severity }: { severity: NotificationSeverity }) {
  return (
    <span
      className={`inline-block w-1.5 h-1.5 rounded-full ${SEVERITY_DOT[severity] ?? SEVERITY_DOT.info}`}
      title={`Severity: ${severity}`}
    />
  );
}

/**
 * Render an actionable button from a NotificationAction spec. Used in the
 * detail modal (and any surface that wants inline action buttons).
 *
 * - `link` actions navigate to `url` via react-router (if app-relative) or
 *   open in a new tab (if absolute).
 * - `post` actions POST to `endpoint` with the given `method`, then refresh
 *   the inbox. Reserved for future server-side actions (e.g. acknowledge).
 */
export function actionHandlerFor(
  action: NotificationAction,
  handlers: {
    navigate: (url: string) => void;
    onPost?: (action: NotificationAction) => Promise<void> | void;
  }
) {
  return async () => {
    if (action.type === 'link' && action.url) {
      handlers.navigate(action.url);
      return;
    }
    if (action.type === 'post' && action.endpoint && handlers.onPost) {
      await handlers.onPost(action);
    }
  };
}

export function actionClassName(style?: string): string {
  switch (style) {
    case 'primary':
      return 'bg-blue-600 hover:bg-blue-700 text-white';
    case 'danger':
      return 'bg-red-600 hover:bg-red-700 text-white';
    case 'ghost':
      return 'bg-gray-100 dark:bg-dark-border text-gray-700 dark:text-dark-text hover:bg-gray-200 dark:hover:bg-dark-bg';
    default:
      return 'border border-gray-200 dark:border-dark-border text-gray-700 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-bg';
  }
}
