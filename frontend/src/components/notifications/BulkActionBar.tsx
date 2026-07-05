import { useTranslation } from 'react-i18next';
import { CheckCheck, Trash2, X } from 'lucide-react';

interface Props {
  selectedCount: number;
  onClearSelection: () => void;
  onMarkSelectedRead: () => void;
  onDismissSelected: () => void;
}

export function BulkActionBar({ selectedCount, onClearSelection, onMarkSelectedRead, onDismissSelected }: Props) {
  const { t } = useTranslation();
  if (selectedCount === 0) return null;

  return (
    <div className="sticky top-0 z-10 bg-blue-600 text-white px-4 py-2 rounded-t-2xl flex items-center justify-between gap-3 shadow-md">
      <div className="flex items-center gap-3 text-sm font-semibold">
        <span className="inline-flex items-center justify-center bg-white/20 rounded-full w-6 h-6 text-xs">
          {selectedCount}
        </span>
        {selectedCount === 1
          ? t('notifications.one_selected', { defaultValue: '1 selected' })
          : t('notifications.n_selected', { defaultValue: `${selectedCount} selected` })}
      </div>
      <div className="flex items-center gap-1">
        <button
          onClick={onMarkSelectedRead}
          className="inline-flex items-center gap-1 px-2 py-1 hover:bg-white/20 rounded-lg text-xs font-semibold"
          title={t('notifications.mark_selected_read', { defaultValue: 'Mark selected as read' })}
        >
          <CheckCheck className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">{t('common.mark_read', { defaultValue: 'Read' })}</span>
        </button>
        <button
          onClick={onDismissSelected}
          className="inline-flex items-center gap-1 px-2 py-1 hover:bg-white/20 rounded-lg text-xs font-semibold"
          title={t('notifications.dismiss_selected', { defaultValue: 'Dismiss selected' })}
        >
          <Trash2 className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">{t('common.dismiss', { defaultValue: 'Dismiss' })}</span>
        </button>
        <button
          onClick={onClearSelection}
          className="p-1 hover:bg-white/20 rounded-lg"
          title={t('common.clear_selection', { defaultValue: 'Clear selection' })}
        >
          <X className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}
