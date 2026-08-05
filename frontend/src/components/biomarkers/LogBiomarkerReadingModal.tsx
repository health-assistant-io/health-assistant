import React, { useEffect } from 'react';
import { AddBiomarkerForm } from '../examinations/AddBiomarkerForm';
import { createObservation } from '../../services/observationService';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import type { Biomarker } from '../../types/biomarker';

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
 * ``AddBiomarkerForm`` in a portal and persists via ``createObservation``
 * — no examination required.
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

  // Esc-to-close + body scroll lock while the modal is open.
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
    };
  }, [isOpen, onClose]);

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
    <div
      className="fixed inset-0 z-[1000] flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm animate-in fade-in duration-200"
      role="dialog"
      aria-modal="true"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="bg-white dark:bg-dark-surface w-full max-w-xl rounded-3xl shadow-2xl border border-gray-100 dark:border-dark-border overflow-hidden flex flex-col max-h-[90vh] animate-in zoom-in-95 duration-200">
        <AddBiomarkerForm
          patientId={patientId}
          lockedBiomarker={lockedBiomarker}
          onSubmit={handleSubmit}
          onCancel={onClose}
          showHeader
          showActions
          headerTitleKey="biomarkers.log_reading.title"
          headerSubtitleKey="biomarkers.log_reading.subtitle"
        />
      </div>
    </div>
  );
};
