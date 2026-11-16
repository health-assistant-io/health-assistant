import React from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '../ui/Button';

/**
 * The one action bar for record forms (Cancel · optional Reject · Save).
 *
 * Rendered by the popup shell in `PanelModal.footer` — where the buttons sit
 * outside the form and submit through the `form` attribute — and inline by a
 * form for its HITL chat-card placement.
 */
export interface RecordFormFooterProps {
  canSubmit: boolean;
  submitLabel: string;
  loading?: boolean;
  cancelLabel?: string;
  onCancel?: () => void;
  rejectLabel?: string;
  onReject?: () => void;
  formId?: string;
}

export const RecordFormFooter: React.FC<RecordFormFooterProps> = ({
  canSubmit,
  submitLabel,
  loading = false,
  cancelLabel,
  onCancel,
  rejectLabel,
  onReject,
  formId,
}) => {
  const { t } = useTranslation();

  return (
    <div className="flex items-center gap-2">
      {onReject && (
        <Button
          type="button"
          variant="destructive"
          onClick={onReject}
          disabled={loading}
        >
          {rejectLabel ?? t('common.reject', 'Reject')}
        </Button>
      )}
      <div className="ml-auto flex items-center gap-3">
        {onCancel && (
          <Button type="button" variant="outline" onClick={onCancel} disabled={loading}>
            {cancelLabel ?? t('common.cancel')}
          </Button>
        )}
        <Button type="submit" form={formId} disabled={!canSubmit} loading={loading}>
          {submitLabel}
        </Button>
      </div>
    </div>
  );
};

export default RecordFormFooter;
