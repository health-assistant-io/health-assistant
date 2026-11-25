import { useTranslation } from 'react-i18next';
import { CheckCheck, Trash2 } from 'lucide-react';
import { SelectionBar } from '@neuronection/assistant-ui';

interface Props {
  selectedCount: number;
  onClearSelection: () => void;
  onMarkSelectedRead: () => void;
  onDismissSelected: () => void;
}

const ACTION =
  'inline-flex items-center gap-1 rounded-[var(--as-radius-sm)] px-2 py-1 text-xs font-medium text-[var(--as-fg)] transition-colors hover:bg-[var(--as-primary)]/10 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--as-focus-ring)]';

export function BulkActionBar({ selectedCount, onClearSelection, onMarkSelectedRead, onDismissSelected }: Props) {
  const { t } = useTranslation();
  if (selectedCount === 0) return null;

  const countLabel =
    selectedCount === 1
      ? t('notifications.one_selected', { count: selectedCount, defaultValue: '1 selected' })
      : t('notifications.n_selected', {
          count: selectedCount,
          defaultValue: `${selectedCount} selected`,
        });

  return (
    <SelectionBar
      count={selectedCount}
      onClear={onClearSelection}
      countLabel={countLabel}
      clearLabel={t('common.clear_selection')}
      className="sticky top-0 z-10 rounded-t-2xl rounded-b-none"
    >
      <button
        type="button"
        onClick={onMarkSelectedRead}
        className={ACTION}
        title={t('notifications.mark_selected_read')}
      >
        <CheckCheck className="size-3.5" aria-hidden />
        <span className="hidden sm:inline">{t('common.mark_read')}</span>
      </button>
      <button
        type="button"
        onClick={onDismissSelected}
        className={ACTION}
        title={t('notifications.dismiss_selected')}
      >
        <Trash2 className="size-3.5" aria-hidden />
        <span className="hidden sm:inline">{t('common.dismiss')}</span>
      </button>
    </SelectionBar>
  );
}
