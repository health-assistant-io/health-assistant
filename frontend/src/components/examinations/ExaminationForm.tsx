import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Camera, Plus } from 'lucide-react';
import { DatePicker } from '../ui/DatePicker';
import { RichTextEditor } from '../ui/RichTextEditor';
import { DocumentCard } from '../ui/DocumentCard';
import { AIAssistButton } from '../ui/AIAssistButton';
import { DoctorSelector } from '../ui/DoctorSelector';
import { OrganizationSelector } from '../ui/OrganizationSelector';
import { CategorySelector } from '../ui/CategorySelector';
import { RecordFormFooter } from '../records/RecordFormFooter';
import { isMobileDevice } from '../../utils/deviceUtils';
import { useExaminationReferences } from '../../hooks/useExaminationReferences';

export interface ExaminationFormFile {
  file: File;
  include: boolean;
}

export interface ExaminationFormPayload {
  date: string;
  notes: string;
  patientNotes: string;
  category: string;
  doctorIds: string[];
  organizationId: string | null;
  files: ExaminationFormFile[];
}

export interface ExaminationFormProps {
  patientId: string;
  onSubmit: (payload: ExaminationFormPayload) => Promise<void>;
  onCancel?: () => void;
  /** Owned by the shell; set as the native `<form id>` so a footer button
   *  outside the form (the popup) can submit it through the `form` attribute. */
  formId?: string;
  /** Inline footer for a non-popup placement; the popup shell renders its own
   *  `RecordFormFooter` instead. */
  showActions?: boolean;
  submitLabel?: string;
  /** Optional content rendered at the end of the scrolling field area (e.g.
   *  the "smart & bulk upload" cross-link). */
  afterFields?: React.ReactNode;
  /** Reports validity/loading to the shell so its footer can drive Save. */
  onStateChange?: (state: { canSubmit: boolean; loading: boolean }) => void;
}

const localToday = () => {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

const fieldLabel =
  'text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest px-1';

/**
 * Headless single-examination form: date, documents, clinical notes, category,
 * facility, doctors and patient notes. The caller creates the examination and
 * uploads the returned files; this component owns only the draft + validity.
 */
export const ExaminationForm: React.FC<ExaminationFormProps> = ({
  patientId,
  onSubmit,
  onCancel,
  formId,
  showActions = true,
  submitLabel,
  afterFields,
  onStateChange,
}) => {
  const { t } = useTranslation();
  const references = useExaminationReferences();

  const [date, setDate] = useState(localToday);
  const [notes, setNotes] = useState('');
  const [patientNotes, setPatientNotes] = useState('');
  const [category, setCategory] = useState('');
  const [doctorIds, setDoctorIds] = useState<string[]>([]);
  const [organizationId, setOrganizationId] = useState<string | null>(null);
  const [files, setFiles] = useState<ExaminationFormFile[]>([]);
  const [loading, setLoading] = useState(false);

  const canSubmit = !loading && Boolean(date);

  const reportedState = useRef('');
  useEffect(() => {
    const key = `${canSubmit}|${loading}`;
    if (reportedState.current === key) return;
    reportedState.current = key;
    onStateChange?.({ canSubmit, loading });
  }, [canSubmit, loading, onStateChange]);

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    if (event.target.files) {
      const next = Array.from(event.target.files).map((file) => ({
        file,
        include: true,
      }));
      setFiles((prev) => [...prev, ...next]);
    }
    event.target.value = '';
  };

  const handleSubmit = async (event?: React.FormEvent) => {
    if (event) event.preventDefault();
    if (!canSubmit) return;
    setLoading(true);
    try {
      await onSubmit({
        date,
        notes,
        patientNotes,
        category,
        doctorIds,
        organizationId,
        files,
      });
    } catch (err) {
      console.error('Failed to save examination form', err);
    } finally {
      setLoading(false);
    }
  };

  const createDoctor = async (name: string) => {
    const created = await references.createDoctor(name);
    setDoctorIds((prev) => [...prev, created.id]);
  };

  const createOrganization = async (name: string) => {
    const created = await references.createOrganization(name);
    setOrganizationId(created.id);
  };

  const createCategory = async (name: string) => {
    const created = await references.createCategory(name);
    setCategory(created?.name ?? name);
  };

  return (
    <form
      id={formId}
      onSubmit={handleSubmit}
      className="flex min-h-0 flex-1 flex-col"
    >
      <div className="flex-1 min-h-0 overflow-y-auto space-y-6 p-6">
        <div className="space-y-2">
          <label className={fieldLabel}>{t('examinations.examination_date', 'Examination Date')}</label>
          <DatePicker
            placeholder={t('common.select_date', 'Select date')}
            required
            value={date}
            onChange={setDate}
          />
        </div>

        <div className="space-y-3">
          <label className={fieldLabel}>{t('examinations.documents', 'Documents')}</label>

          {files.length > 0 && (
            <div className="flex flex-wrap gap-4">
              {files.map((entry, index) => (
                <DocumentCard
                  key={`${entry.file.name}-${index}`}
                  file={entry.file}
                  includeInExtraction={entry.include}
                  onRemove={() =>
                    setFiles((prev) => prev.filter((_, i) => i !== index))
                  }
                  onToggleInclusion={() =>
                    setFiles((prev) =>
                      prev.map((f, i) =>
                        i === index ? { ...f, include: !f.include } : f,
                      ),
                    )
                  }
                />
              ))}
            </div>
          )}

          <div className={`grid grid-cols-1 ${isMobileDevice() ? 'sm:grid-cols-2' : ''} gap-4`}>
            <div className="relative">
              <input
                type="file"
                multiple
                onChange={handleFileChange}
                className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                accept=".pdf,.jpg,.jpeg,.png,.docx,.txt,.dcm"
                id="examination-file-upload"
              />
              <label
                htmlFor="examination-file-upload"
                className="flex items-center justify-center px-4 py-3 border-2 border-dashed border-gray-300 dark:border-dark-border rounded-xl hover:border-blue-500 dark:hover:border-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/10 transition-all cursor-pointer text-blue-600 dark:text-blue-400 font-bold text-sm"
              >
                <Plus className="h-5 w-5 mr-2" />
                {files.length === 0
                  ? t('examinations.select_documents', 'Select Documents')
                  : t('examinations.add_more_documents', 'Add More')}
              </label>
            </div>

            {isMobileDevice() && (
              <div className="relative">
                <input
                  type="file"
                  accept="image/*"
                  capture="environment"
                  onChange={handleFileChange}
                  className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                  id="examination-camera-upload"
                />
                <label
                  htmlFor="examination-camera-upload"
                  className="flex items-center justify-center px-4 py-3 border-2 border-dashed border-indigo-200 dark:border-indigo-900/30 rounded-xl hover:border-indigo-500 dark:hover:border-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-900/10 transition-all cursor-pointer text-indigo-600 dark:text-indigo-400 font-bold text-sm"
                >
                  <Camera className="h-5 w-5 mr-2" />
                  {t('examinations.take_photo', 'Take Photo')}
                </label>
              </div>
            )}
          </div>
        </div>

        <div className="space-y-2">
          <label className={fieldLabel}>{t('examinations.clinical_notes', 'Clinical Notes')}</label>
          <RichTextEditor
            value={notes}
            onChange={setNotes}
            placeholder={t(
              'examinations.clinical_notes_placeholder',
              'Add any clinical notes about this examination',
            )}
            minHeight="120px"
            compact
          />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <label className={fieldLabel}>
                {t('examination_detail.header.category', 'Category')}
              </label>
              <AIAssistButton
                taskType="magic_fill_examination"
                context={{ patientId }}
                showLabel={false}
                placeholder={t(
                  'examinations.magic_fill_category_hint',
                  'Describe the visit to categorize...',
                )}
                onSuggestedData={(data) => {
                  if (data.category) setCategory(data.category);
                }}
              />
            </div>
            <CategorySelector
              categories={references.categories}
              selectedName={category}
              onSelect={setCategory}
              onCreate={createCategory}
              placeholder={t('examination_detail.header.select_category')}
            />
          </div>

          <div className="space-y-2">
            <label className={fieldLabel}>{t('organizations.title')}</label>
            <OrganizationSelector
              organizations={references.organizations}
              selectedId={organizationId}
              onSelect={setOrganizationId}
              onCreate={createOrganization}
            />
          </div>
        </div>

        <div className="space-y-2">
          <label className={fieldLabel}>
            {t('examinations.attending_doctors_optional', 'Attending Doctors (Optional)')}
          </label>
          <DoctorSelector
            doctors={references.doctors}
            selectedIds={doctorIds}
            onSelect={(id) => setDoctorIds((prev) => [...prev, id])}
            onDeselect={(id) =>
              setDoctorIds((prev) => prev.filter((value) => value !== id))
            }
            onCreateDoctor={createDoctor}
            placeholder={t('examinations.attending_physician')}
          />
        </div>

        <div className="space-y-2">
          <label className={fieldLabel}>
            {t('examinations.patient_notes', 'Patient Notes')}
          </label>
          <textarea
            rows={3}
            value={patientNotes}
            onChange={(e) => setPatientNotes(e.target.value)}
            placeholder={t(
              'examinations.patient_notes_placeholder',
              'How do you feel? Why did you visit the doctor?',
            )}
            className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-sm focus:ring-2 focus:ring-blue-500 outline-hidden dark:text-dark-text resize-none"
          />
        </div>

        {afterFields}
      </div>

      {showActions && (
        <div className="flex shrink-0 items-center border-t border-gray-50 bg-gray-50 px-6 py-4 dark:border-dark-border dark:bg-dark-bg/50">
          <RecordFormFooter
            formId={formId}
            canSubmit={canSubmit}
            loading={loading}
            submitLabel={
              submitLabel ?? t('examinations.save', 'Save Examination')
            }
            cancelLabel={t('common.cancel')}
            onCancel={onCancel}
          />
        </div>
      )}
    </form>
  );
};

export default ExaminationForm;
