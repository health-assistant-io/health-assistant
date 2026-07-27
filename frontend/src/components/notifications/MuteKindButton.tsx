import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { BellOff, Clock, ExternalLink } from 'lucide-react';
import { toast } from 'react-toastify';
import type { NotificationPreferencesHint } from '../../services/notificationService';
import { notificationService } from '../../services/notificationService';

interface Props {
  hint: NotificationPreferencesHint;
  /** Compact variant for inline menus (bell dropdown); default = full button. */
  variant?: 'button' | 'compact';
  onMuted?: () => void;
}

/**
 * Inline "Turn off this kind" control rendered on a notification.
 *
 * Reads the per-kind hint stamped by the backend (``payload.preferences``)
 * and calls the unified preference endpoint. When ``mutable`` is false
 * (safety-critical notifications) the mute action is hidden — only the
 * "Notification settings" link remains. Never derives kind semantics itself.
 */
export function MuteKindButton({ hint, variant = 'button', onMuted }: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);

  const handleMute = async () => {
    setBusy(true);
    try {
      await notificationService.setPreference(hint.kind_id, false);
      toast.success(
        t('notifications.muted_toast', {
          defaultValue: 'Turned off “{{label}}”',
          label: hint.label,
        })
      );
      onMuted?.();
    } catch (err: any) {
      toast.error(
        err?.response?.data?.detail ??
          t('notifications.mute_failed', { defaultValue: 'Could not update preference' })
      );
    } finally {
      setBusy(false);
    }
  };

  const manageLabel = t('notifications.manage_settings', {
    defaultValue: 'Notification settings',
  });

  if (variant === 'compact') {
    return (
      <>
        {hint.mutable && (
          <button
            onClick={handleMute}
            disabled={busy}
            className="w-full flex items-center gap-2 px-3 py-2 text-xs font-semibold text-gray-600 dark:text-dark-muted hover:bg-gray-100 dark:hover:bg-dark-border transition-colors disabled:opacity-50"
          >
            {busy ? (
              <Clock className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <BellOff className="w-3.5 h-3.5" />
            )}
            {t('notifications.turn_off_kind', {
              defaultValue: 'Turn off this kind',
            })}
          </button>
        )}
        <button
          onClick={() => navigate(hint.manage_url)}
          className="w-full flex items-center gap-2 px-3 py-2 text-xs font-semibold text-gray-600 dark:text-dark-muted hover:bg-gray-100 dark:hover:bg-dark-border transition-colors"
        >
          <ExternalLink className="w-3.5 h-3.5" />
          {manageLabel}
        </button>
      </>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      {hint.mutable && (
        <button
          onClick={handleMute}
          disabled={busy}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed text-gray-600 dark:text-dark-muted bg-gray-100 dark:bg-dark-border hover:bg-gray-200 dark:hover:bg-dark-hover"
        >
          {busy ? (
            <Clock className="w-3 h-3 animate-spin" />
          ) : (
            <BellOff className="w-3 h-3" />
          )}
          {t('notifications.turn_off_kind', {
            defaultValue: 'Turn off “{{label}}”',
            label: hint.label,
          })}
        </button>
      )}
      <button
        onClick={() => navigate(hint.manage_url)}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20"
      >
        <ExternalLink className="w-3 h-3" />
        {manageLabel}
      </button>
    </div>
  );
}
