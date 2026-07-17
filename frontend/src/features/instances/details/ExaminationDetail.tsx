/**
 * ExaminationDetail — the single-record detail view for an examination
 * instance, shown in `InstanceCard`'s "open" overlay. Reuses the existing
 * {@link ExaminationPreview} verbatim (the same single source of truth the
 * examinations list page and `ExaminationView`'s detail pane use), fed the
 * FULL exam (`getExamination`, so observations/biomarkers/notes/impressions/
 * diagnoses render) plus its documents (`getExaminationDocuments`).
 *
 * No new UI is invented here — this is just the fetch + delegate wrapper that
 * `ExaminationView`'s detail pane already inlines, factored out so a lone
 * record can render richly outside the browse modal.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle } from 'lucide-react';
import { ExaminationPreview } from '../../../components/examinations/ExaminationPreview';
import { LoadingState } from '../../../components/ui/LoadingState';
import {
  getExamination,
  getExaminationDocuments,
} from '../../../services/examinationService';
import type { InstanceDetailProps } from '../../../components/instances/detailViewRegistry';

export const ExaminationDetail: React.FC<InstanceDetailProps> = ({ id }) => {
  const { t } = useTranslation();
  const [exam, setExam] = useState<any>(null);
  const [documents, setDocuments] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      setLoading(true);
      setError(false);
      try {
        const [fullExam, docs] = await Promise.all([
          getExamination(id),
          getExaminationDocuments(id),
        ]);
        if (cancelled) return;
        // ExaminationPreview expects valid examination_date/created_at — coerce
        // so a missing date never throws (mirrors ExaminationView's safeExam).
        setExam({
          ...fullExam,
          examination_date:
            fullExam.examination_date || fullExam.created_at || new Date().toISOString(),
          created_at: fullExam.created_at || new Date().toISOString(),
        });
        setDocuments(docs);
      } catch {
        if (!cancelled) {
          setError(true);
          setExam(null);
          setDocuments([]);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    run();
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (loading) {
    return <LoadingState variant="section" />;
  }
  if (error || !exam) {
    return (
      <div className="flex flex-col items-center justify-center py-10 text-center">
        <AlertTriangle className="w-8 h-8 text-amber-400 mb-2" />
        <p className="text-sm text-gray-500 dark:text-dark-muted">
          {t('instances.card_unavailable', 'Record unavailable')}
        </p>
      </div>
    );
  }

  return (
    <ExaminationPreview
      selectedExam={exam}
      examDocuments={documents}
      onDocumentClick={() => undefined}
      onInfoClick={() => undefined}
      hideHeader
    />
  );
};
