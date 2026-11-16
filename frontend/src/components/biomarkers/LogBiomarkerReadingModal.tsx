import React, { useId, useState } from 'react';
import { AddBiomarkerForm } from '../examinations/AddBiomarkerForm';
import { createObservation } from '../../services/observationService';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import { Activity } from 'lucide-react';
import type { Biomarker } from '../../types/biomarker';
import { Modal } from '../ui/Modal';
import { RecordFormFooter } from '../records/RecordFormFooter';
import { RecordFormIcon } from '../records/RecordFormIcon';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  patientId: string;
  /** Optional — pre-select a biomarker definition (used by the
   *  BiomarkerDetail "Log Reading" action). When omitted the user lands on
   *  the catalog search step. */
  lockedBiomarker?: Biomarker;
  /** Callback fired after a successful save (parent refreshes data). The
   *  modal closes itself before invoking this. */
  onSuccess?: () => void;
}

/**
 * Standalone biomarker-reading entry modal. Wraps the headless
 * ``AddBiomarkerForm`` in the record-form popup shell and persists via
 * ``createObservation`` — no examination required.
 *
 * Used from three entry points:
 *   • ``BiomarkerDetail`` StickyToolbar (passes ``lockedBiomarker``)
 *   • ``BiomarkerHistoryTab`` header (passes ``lockedBiomarker``)
 *   • ``BiomarkerTrends`` main page (no lock — catalog search)
 *
 * The resulting Observation has ``examination_id = null`` and the
 * ``effective_datetime`` / ``method`` set from the form's standalone-mode
 * fields, so the reading sorts correctly in the longitudinal trend and
 * shows a "manual" provenance chip in the history table.
 */
export const LogBiomarkerReadingModal: React.FC<Props> = ({
  isOpen,
  onClose,
  patientId,
  lockedBiomarker,
  onSuccess,
}) => {
  const { t } = useTranslation();
  const formId = useId();
  const [formState, setFormState] = useState({ canSubmit: false, loading: false });

  if (!isOpen) return null;

  const handleSubmit = async (observation: Parameters<React.ComponentProps<typeof AddBiomarkerForm>['onSubmit']>[0]) => {
    try {
      await createObservation(observation as any);
      toast.success(
        lockedBiomarker
          ? t('biomarkers.log_reading.saved_for', { name: lockedBiomarker.name })
          : t('biomarkers.log_reading.saved_default', 'Reading saved'),
      );
      onSuccess?.();
      onClose();
    } catch (err) {
      console.error('Failed to save biomarker reading', err);
      toast.error(t('biomarkers.log_reading.save_error', 'Failed to save reading'));
      throw err;
    }
  };

  return (
    <Modal
      open={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title={t('biomarkers.log_reading.title')}
      description={t('biomarkers.log_reading.subtitle')}
      headerIcon={<RecordFormIcon icon={Activity} tone="blue" />}
      closeLabel={t('common.close')}
      footer={
        <RecordFormFooter
          formId={formId}
          canSubmit={formState.canSubmit}
          loading={formState.loading}
          submitLabel={t('examination_detail.add_biomarker.add_result')}
          cancelLabel={t('common.cancel')}
          onCancel={onClose}
        />
      }
      bodyFill
      bodyClassName="p-0"
      size="md"
    >
      <AddBiomarkerForm
        patientId={patientId}
        lockedBiomarker={lockedBiomarker}
        onSubmit={handleSubmit}
        onCancel={onClose}
        formId={formId}
        showActions={false}
        onStateChange={setFormState}
      />
    </Modal>
  );
};
