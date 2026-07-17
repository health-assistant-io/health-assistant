/**
 * ExaminationView — the per-type browse view for examination instances.
 *
 * Mirrors the examinations list page exactly (no reinvention, no stripped
 * variant):
 *   - list pane: `ExaminationCard` with the SAME props as `ExaminationList`
 *     (full card — category, tags, status, doctors, events). No pick checkbox
 *     — the selected (previewed) card is highlighted with a solid focus border
 *     via `isSelected`, matching the list page. A small Add/Added button is
 *     overlaid on each card for direct picking.
 *   - preview pane: `ExaminationPreview` fed the FULL exam (`getExamination`,
 *     so observations/biomarkers/notes render — the list page does the same)
 *     plus its documents (`getExaminationDocuments`).
 *
 * Selection model (the picker's "select then add" flow):
 *   - clicking a card selects it for preview (solid border focus);
 *   - the per-card Add button or the preview toolbar's "Add to selection"
 *     button adds it.
 */
import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, Check, Inbox } from 'lucide-react';
import { MasterDetailLayout } from '../../../components/ui/MasterDetailLayout';
import { FilterBar } from '../../../components/ui/filters/FilterBar';
import { ExaminationCard } from '../../../components/examinations/ExaminationCard';
import { ExaminationPreview } from '../../../components/examinations/ExaminationPreview';
import {
  getExamination,
  getExaminationDocuments,
} from '../../../services/examinationService';
import { useInstanceFacets } from '../facets/useInstanceFacets';
import type { InstanceViewProps } from '../../../components/instances/types';
import type { Examination } from '../../../types/clinical';

export const ExaminationView: React.FC<InstanceViewProps<Examination>> = ({
  items,
  pickedIds,
  onTogglePick,
  loading,
  hasMore,
  loadingMore,
  onLoadMore,
}) => {
  const { t } = useTranslation();
  // Shared facets (category + status) — same definitions the generic browser
  // path and the listing page use. The view owns its filter bar because it
  // takes over the browse modal.
  const { facets, filter, filtered } = useInstanceFacets<Examination>('examination', items);
  const [selectedId, setSelectedId] = useState<string | null>(items[0]?.id ?? null);
  // Full exam (with observations/biomarkers) + documents for the preview.
  const [fullExam, setFullExam] = useState<any>(null);
  const [examDocuments, setExamDocuments] = useState<any[]>([]);

  // Fetch the full exam + its documents when the selection changes — the same
  // pair of calls the examinations list page makes, so the preview renders
  // biomarkers, notes, impressions, diagnoses and documents.
  useEffect(() => {
    let cancelled = false;
    const fetchDetail = async () => {
      if (!selectedId) {
        setFullExam(null);
        setExamDocuments([]);
        return;
      }
      try {
        const [exam, docs] = await Promise.all([
          getExamination(selectedId),
          getExaminationDocuments(selectedId),
        ]);
        if (cancelled) return;
        setFullExam(exam);
        setExamDocuments(docs);
      } catch {
        if (!cancelled) {
          setFullExam(null);
          setExamDocuments([]);
        }
      }
    };
    fetchDetail();
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  // The list item is available immediately; the full exam (with observations/
  // biomarkers) arrives a moment later via getExamination. Render the list item
  // right away and upgrade to the full exam once it loads — this avoids the
  // ExaminationPreview null-state flash (the faded 'select to view' placeholder)
  // that appeared on first click before the fetch resolved.
  const selectedListExam = items.find((i) => i.id === selectedId) ?? null;
  const previewExam =
    fullExam && fullExam.id === selectedId ? fullExam : selectedListExam;

  // ExaminationPreview requires valid examination_date/created_at — coerce so a
  // missing date never throws inside the picker.
  const safeExam = previewExam
    ? {
        ...previewExam,
        examination_date:
          previewExam.examination_date || (previewExam as any).created_at || new Date().toISOString(),
        created_at:
          (previewExam as any).created_at || new Date().toISOString(),
      }
    : null;

  const isPicked = (id: string) => pickedIds.includes(id);

  if (loading && items.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400">
        {t('common.loading', 'Loading…')}
      </div>
    );
  }

  const AddButton: React.FC<{ exam: Examination }> = ({ exam }) => {
    const picked = isPicked(exam.id);
    return (
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onTogglePick(exam);
        }}
        className={`absolute bottom-2 right-2 z-20 inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-md shadow-md ring-1 ring-black/5 transition-colors ${
          picked
            ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300'
            : 'bg-blue-600 text-white hover:bg-blue-700'
        }`}
      >
        {picked ? (
          <>
            <Check className="w-3 h-3" /> {t('instances.picker_added', 'Added')}
          </>
        ) : (
          <>
            <Plus className="w-3 h-3" /> {t('common.add', 'Add')}
          </>
        )}
      </button>
    );
  };

  return (
    <MasterDetailLayout
      withListStyling={false}
      listWidth="lg:w-[360px] xl:w-[400px]"
      list={
        <div className="flex flex-col h-full gap-2 min-h-0">
          {facets.length > 0 && (
            <FilterBar
              facets={facets as any}
              filter={filter}
              items={items}
              showActivePills
              resultCount={filtered.length}
              totalCount={items.length}
            />
          )}
          <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar pr-1">
            {filtered.map((exam) => (
              <div key={exam.id} className="relative mb-2">
                <AddButton exam={exam} />
                <ExaminationCard
                  examination={exam as any}
                  categoryIconOnly
                  allowEventInteraction={false}
                  isSelected={selectedId === exam.id}
                  onClick={() => setSelectedId(exam.id)}
                />
              </div>
            ))}
            {hasMore && (
              <div className="flex justify-center py-3">
                <button
                  type="button"
                  onClick={onLoadMore}
                  disabled={loadingMore}
                  className="px-4 py-2 text-sm font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-60"
                >
                  {loadingMore
                    ? t('common.loading', 'Loading…')
                    : t('common.load_more', 'Load more')}
                </button>
              </div>
            )}
          </div>
        </div>
      }
      detail={
        safeExam ? (
          <div className="flex flex-col h-full min-h-0">
            {/* Preview toolbar: the explicit select→add affordance */}
            <div className="shrink-0 flex items-center justify-between gap-2 px-6 py-3 border-b border-gray-100 dark:border-dark-border">
              <span className="text-xs font-bold uppercase tracking-widest text-gray-400">
                {t('instances.preview', 'Preview')}
              </span>
              <button
                type="button"
                onClick={() => selectedListExam && onTogglePick(selectedListExam)}
                className={`inline-flex items-center gap-1 px-3 py-1.5 text-xs font-bold rounded-lg transition-colors ${
                  isPicked(safeExam.id)
                    ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300'
                    : 'bg-blue-600 text-white hover:bg-blue-700'
                }`}
              >
                {isPicked(safeExam.id) ? (
                  <>
                    <Check className="w-3.5 h-3.5" /> {t('instances.picker_added', 'Added')}
                  </>
                ) : (
                  <>
                    <Plus className="w-3.5 h-3.5" /> {t('instances.add_to_selection', 'Add to selection')}
                  </>
                )}
              </button>
            </div>
            <div className="flex-1 min-h-0 overflow-hidden">
              <ExaminationPreview
                selectedExam={safeExam}
                examDocuments={examDocuments}
                onDocumentClick={() => undefined}
                onInfoClick={() => undefined}
                hideHeader
              />
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <Inbox className="w-8 h-8 text-gray-300 dark:text-gray-600 mb-2" />
            <p className="text-sm text-gray-400">
              {t('instances.preview_empty', 'Select a record to preview')}
            </p>
          </div>
        )
      }
    />
  );
};
