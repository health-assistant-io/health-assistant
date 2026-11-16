import React, { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FileText, Sparkles } from 'lucide-react';
import { toast } from 'react-toastify';
import { Modal } from '../ui/Modal';
import { RecordFormFooter } from '../records/RecordFormFooter';
import { RecordFormIcon } from '../records/RecordFormIcon';
import { ExaminationForm, type ExaminationFormPayload } from './ExaminationForm';
import { createExamination } from '../../services/examinationService';
import { uploadDocument } from '../../services/documentService';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  patientId: string;
  /** Fired after the examination (and its documents) are saved. */
  onSuccess: (examinationId: string) => void;
  /** Opens the smart/bulk upload page (the advanced ingestion route). */
  onAdvanced?: () => void;
}

/**
 * Single-examination create popup: the same fields as the old page default —
 * documents included — on the shared record-form shell. Smart extraction and
 * bulk grouping stay on the advanced upload page, which this popup links to.
 */
export const ExaminationModal: React.FC<Props> = ({
  isOpen,
  onClose,
  patientId,
  onSuccess,
  onAdvanced,
}) => {
  const { t } = useTranslation();
  const formId = useId();
  const [formState, setFormState] = useState({ canSubmit: false, loading: false });

  if (!isOpen) return null;

  const handleSubmit = async (payload: ExaminationFormPayload) => {
    let examination: { id: string };
    try {
      examination = await createExamination({
        patient_id: patientId,
        examination_date: payload.date,
        notes: payload.notes || undefined,
        patient_notes: payload.patientNotes || undefined,
        category: payload.category || undefined,
        doctor_ids: payload.doctorIds,
        organization_id: payload.organizationId || undefined,
        auto_extract_metadata: false,
      });
    } catch (err) {
      console.error('Failed to create examination', err);
      toast.error(t('examinations.save_failed', 'Failed to save examination'));
      throw err;
    }

    let failedUploads = 0;
    for (const entry of payload.files) {
      try {
        await uploadDocument(entry.file, patientId, examination.id, entry.include);
      } catch (err) {
        failedUploads += 1;
        console.error('Failed to upload examination document', err);
      }
    }
    if (failedUploads > 0) {
      toast.error(
        t('examinations.upload_partial_failed', {
          count: failedUploads,
          defaultValue: 'Some documents could not be uploaded. You can add them from the examination page.',
        }),
      );
    }

    onSuccess(examination.id);
    onClose();
  };

  return (
    <Modal
      open={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title={t('common.new_examination')}
      description={t(
        'examinations.manual_subtitle',
        'Upload documents and record the visit',
      )}
      headerIcon={<RecordFormIcon icon={FileText} tone="blue" />}
      closeLabel={t('common.close')}
      footer={
        <RecordFormFooter
          formId={formId}
          canSubmit={formState.canSubmit}
          loading={formState.loading}
          submitLabel={t('examinations.save', 'Save Examination')}
          cancelLabel={t('common.cancel')}
          onCancel={onClose}
        />
      }
      bodyFill
      bodyClassName="p-0"
      size="md"
    >
      <ExaminationForm
        patientId={patientId}
        formId={formId}
        showActions={false}
        onStateChange={setFormState}
        onSubmit={handleSubmit}
        onCancel={onClose}
        afterFields={
          onAdvanced ? (
            <div className="border-t border-gray-100 pt-4 dark:border-dark-border">
              <p className="text-[11px] font-medium text-gray-500 dark:text-dark-muted">
                {t(
                  'examinations.advanced_upload_hint',
                  'Many documents, or want the AI to extract the details?',
                )}
              </p>
              <button
                type="button"
                onClick={onAdvanced}
                className="mt-1 inline-flex items-center gap-1.5 text-xs font-bold text-blue-600 hover:underline dark:text-blue-400"
              >
                <Sparkles className="w-3.5 h-3.5" />
                {t('examinations.smart_bulk_upload', 'Smart & bulk upload')}
              </button>
            </div>
          ) : null
        }
      />
    </Modal>
  );
};

export default ExaminationModal;
