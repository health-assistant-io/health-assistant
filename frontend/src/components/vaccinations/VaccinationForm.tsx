/**
 * VaccinationForm — create/edit a patient immunization record.
 *
 * Mirrors `MedicationForm`'s architecture (self-contained header + scrollable
 * body + footer) but composes the shared primitives instead of reimplementing
 * them:
 *  - `CatalogItemPicker` (single, type `vaccine`) — pick the vaccine definition.
 *  - "Define new" inline panel — create a vaccine catalog entry on the fly.
 *  - `InstanceField` (single, type `examination`) — link the visit the dose was
 *    administered at (the `examination_id` FK).
 *  - `DatePicker` for the administered date.
 *  - `LinksSection` (srcType `immunization`) — graph links on the catalog entry.
 *
 * The form owns the draft; `onSubmit` receives a typed payload and the caller
 * (`VaccinationModal`) performs the API writes + link persistence.
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Syringe,
  Search,
  Plus,
  Save,
  X,
  Calendar,
  Info,
  Hash,
  Package,
  Building2,
} from 'lucide-react';
import type { PatientImmunization } from '../../types/vaccine';
import type { CatalogSelection } from '../../types/catalog';
import { CatalogItemPicker } from '../catalog/CatalogItemPicker';
import { InstanceField } from '../instances/InstanceField';
import '../../features/instances/adapters'; // registers instance adapters
import { DatePicker } from '../ui/DatePicker';
import { LinksSection } from '../ai/hitl/LinksSection';
import { IMMUNIZATION_STATUSES } from './vaccinationStatus';

/** AI/HITL prefill shape (ready for a future `propose_vaccine` tool). */
export interface VaccinationFormPrefill {
  name?: string;
  catalog_id?: string | null;
  matched?: boolean;
  is_new?: boolean;
  administered_at?: string;
  dose_number?: string;
  lot_number?: string;
  manufacturer?: string;
  location?: string;
  note?: string;
  status?: string;
  examination_id?: string | null;
  links?: CatalogSelection[];
}

export interface VaccinationFormPayload {
  status: string;
  vaccine_code: { text: string; catalog_id?: string | null };
  examination_id?: string | null;
  administered_at?: string | null;
  dose_number?: string | null;
  lot_number?: string | null;
  manufacturer?: string | null;
  location?: string | null;
  note?: string | null;
  /** When true, the caller creates a vaccine catalog entry from the name
   *  (disease association is handled in the catalog via LinksSection/PREVENTS,
   *  not as free text here). */
  is_new_catalog_entry?: boolean;
  /** Graph links for the catalog entry; persisted by the caller. */
  links: CatalogSelection[];
}

export interface VaccinationFormProps {
  patientId: string;
  immunization?: PatientImmunization;
  prefill?: VaccinationFormPrefill;
  onSubmit: (payload: VaccinationFormPayload) => Promise<void>;
  onCancel?: () => void;
  showHeader?: boolean;
  showActions?: boolean;
  submitLabel?: string;
}

const EMPTY = {
  status: 'completed',
  administered_at: '',
  dose_number: '',
  lot_number: '',
  manufacturer: '',
  location: '',
  note: '',
  examination_id: '' as string,
};

export const VaccinationForm: React.FC<VaccinationFormProps> = ({
  patientId,
  immunization,
  prefill,
  onSubmit,
  onCancel,
  showHeader = true,
  showActions = true,
  submitLabel,
}) => {
  const { t } = useTranslation();
  const [selection, setSelection] = useState<CatalogSelection[]>([]);
  const [isAddingNew, setIsAddingNew] = useState(false);
  const [newName, setNewName] = useState('');
  const [links, setLinks] = useState<CatalogSelection[]>([]);
  const [loading, setLoading] = useState(false);
  const [form, setForm] = useState({ ...EMPTY });

  // Hydrate from the record being edited or the AI prefill.
  useEffect(() => {
    if (immunization) {
      setForm({
        status: immunization.status ?? 'completed',
        administered_at: immunization.administered_at
          ? immunization.administered_at.split('T')[0]
          : '',
        dose_number: immunization.dose_number ?? '',
        lot_number: immunization.lot_number ?? '',
        manufacturer: immunization.manufacturer ?? '',
        location: immunization.location ?? '',
        note: immunization.note ?? '',
        examination_id: immunization.examination_id ?? '',
      });
      setSelection([]);
      setIsAddingNew(false);
      setLinks([]);
    } else if (prefill) {
      setForm((prev) => ({
        ...prev,
        status: prefill.status || 'completed',
        administered_at: prefill.administered_at
          ? prefill.administered_at.split('T')[0]
          : '',
        dose_number: prefill.dose_number ?? '',
        lot_number: prefill.lot_number ?? '',
        manufacturer: prefill.manufacturer ?? '',
        location: prefill.location ?? '',
        note: prefill.note ?? '',
        examination_id: prefill.examination_id ?? '',
      }));
      setLinks(Array.isArray(prefill.links) ? prefill.links : []);
      if (prefill.matched && prefill.catalog_id) {
        setSelection([
          { type: 'vaccine', id: prefill.catalog_id, label: prefill.name ?? '' },
        ]);
        setIsAddingNew(false);
      } else if (prefill.is_new) {
        setSelection([]);
        setNewName(prefill.name ?? '');
        setIsAddingNew(true);
      }
    } else {
      setForm({ ...EMPTY });
      setSelection([]);
      setIsAddingNew(false);
      setNewName('');
      setLinks([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [immunization, prefill]);

  const canSubmit =
    !loading &&
    (immunization
      ? true
      : isAddingNew
        ? newName.trim().length > 0
        : selection.length > 0);

  const handleSubmit = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!canSubmit) return;
    setLoading(true);
    try {
      const payload: VaccinationFormPayload = {
        status: form.status,
        vaccine_code: immunization
          ? immunization.vaccine_code
          : {
              text: isAddingNew ? newName : (selection[0]?.label ?? newName),
              catalog_id: isAddingNew ? null : (selection[0]?.id ?? null),
            },
        examination_id: form.examination_id || null,
        administered_at: form.administered_at
          ? new Date(form.administered_at).toISOString()
          : null,
        dose_number: form.dose_number || null,
        lot_number: form.lot_number || null,
        manufacturer: form.manufacturer || null,
        location: form.location || null,
        note: form.note || null,
        is_new_catalog_entry: !immunization && isAddingNew,
        links,
      };
      await onSubmit(payload);
    } catch (err) {
      console.error('Failed to save vaccination form', err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {showHeader && (
        <div className="px-8 py-6 border-b border-gray-50 dark:border-dark-border flex items-center justify-between shrink-0 bg-white dark:bg-dark-surface">
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-rose-50 dark:bg-rose-900/30 rounded-xl">
              <Syringe className="w-6 h-6 text-rose-600 dark:text-rose-400" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-gray-900 dark:text-dark-text">
                {immunization
                  ? t('vaccinations.modal.update_title')
                  : t('vaccinations.modal.new_title')}
              </h2>
              <p className="text-xs text-gray-500 dark:text-dark-muted font-medium uppercase tracking-widest mt-0.5">
                {t('vaccinations.modal.subtitle')}
              </p>
            </div>
          </div>
          {onCancel && (
            <button
              type="button"
              onClick={onCancel}
              className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors"
            >
              <X className="w-5 h-5 text-gray-400" />
            </button>
          )}
        </div>
      )}

      <form
        onSubmit={handleSubmit}
        className="flex-1 min-h-0 overflow-y-auto p-8 space-y-8 custom-scrollbar"
      >
        {/* Vaccine selection — only when creating */}
        {!immunization && (
          <div className="space-y-4">
            <label className="text-xs font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest px-1 flex items-center">
              <Search className="w-3 h-3 mr-2" />
              {t('vaccinations.modal.select_from_catalog')}
            </label>

            {!isAddingNew && (
              <>
                <CatalogItemPicker
                  mode="single"
                  allowedTypes={['vaccine']}
                  value={selection}
                  onChange={setSelection}
                  placeholder={t('vaccinations.modal.search_placeholder')}
                  displayMode="cards"
                  block
                />
                {selection.length === 0 && (
                  <button
                    type="button"
                    onClick={() => setIsAddingNew(true)}
                    className="w-full text-left px-6 py-5 bg-rose-50/50 dark:bg-rose-900/10 hover:bg-rose-50 dark:hover:bg-rose-900/20 flex items-center space-x-3 text-rose-600 rounded-2xl border border-dashed border-rose-200"
                  >
                    <div className="p-2 bg-rose-600 text-white rounded-xl">
                      <Plus className="w-4 h-4" />
                    </div>
                    <div>
                      <p className="text-sm font-bold">
                        {t('vaccinations.modal.define_new')}
                      </p>
                      <p className="text-[10px] font-bold uppercase tracking-widest">
                        {t('vaccinations.modal.add_custom')}
                      </p>
                    </div>
                  </button>
                )}
              </>
            )}

            {isAddingNew && (
              <div className="p-6 bg-rose-50/30 dark:bg-rose-900/10 rounded-2xl border border-rose-100/50 dark:border-rose-900/30 space-y-4 animate-in zoom-in-95">
                <div className="flex items-center justify-between">
                  <h4 className="text-[10px] font-bold text-rose-600 uppercase tracking-widest">
                    {t('vaccinations.modal.define_new')}
                  </h4>
                  <button
                    type="button"
                    onClick={() => {
                      setIsAddingNew(false);
                      setNewName('');
                    }}
                    className="px-3 py-1.5 text-[10px] font-bold text-rose-600 dark:text-rose-400 uppercase tracking-widest hover:underline"
                  >
                    {t('medications.modal.change')}
                  </button>
                </div>
                <div>
                  <label className="block text-[10px] font-bold text-gray-400 uppercase mb-1.5 ml-1">
                    {t('vaccinations.modal.name_label')}
                  </label>
                  <input
                    type="text"
                    placeholder={t('vaccinations.modal.name_placeholder')}
                    className="w-full px-4 py-3 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-sm focus:ring-2 focus:ring-rose-500 outline-none dark:text-dark-text"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    autoFocus
                  />
                </div>
              </div>
            )}
          </div>
        )}

        {immunization && (
          <div className="p-6 bg-gray-50 dark:bg-dark-bg rounded-2xl border border-gray-100 dark:border-dark-border">
            <p className="text-xs font-bold text-gray-400 uppercase tracking-widest mb-1">
              {t('vaccinations.modal.editing_record_for')}
            </p>
            <h3 className="text-xl font-bold text-gray-900 dark:text-dark-text">
              {immunization.vaccine_code?.text}
            </h3>
          </div>
        )}

        {/* Administration details */}
        <div className="space-y-6">
          <div className="flex items-center space-x-2 border-b border-gray-50 dark:border-dark-border pb-2">
            <Calendar className="w-4 h-4 text-rose-500" />
            <h3 className="text-sm font-bold text-gray-900 dark:text-dark-text tracking-tight">
              {t('vaccinations.modal.administration_section')}
            </h3>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 bg-gray-50/50 dark:bg-dark-bg/30 p-6 rounded-2xl border border-gray-50 dark:border-dark-border">
            <div className="space-y-3">
              <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1 flex items-center">
                <Calendar className="w-3 h-3 mr-2" />
                {t('vaccinations.modal.administered_on')}
              </label>
              <DatePicker
                value={form.administered_at}
                onChange={(d) => setForm({ ...form, administered_at: d })}
                allowClear
              />
            </div>
            <div className="space-y-3">
              <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1">
                {t('vaccinations.modal.status')}
              </label>
              <select
                className="w-full px-4 py-3 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-sm font-bold focus:ring-2 focus:ring-rose-500/40 outline-none dark:text-dark-text"
                value={form.status}
                onChange={(e) => setForm({ ...form, status: e.target.value })}
              >
                {IMMUNIZATION_STATUSES.map((s) => (
                  <option key={s.value} value={s.value}>
                    {t(`vaccinations.modal.status_option.${s.value}`, s.value)}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Linked examination (instance selector) */}
          <div className="space-y-2">
            <InstanceField
              label={t('vaccinations.modal.linked_examination')}
              allowedTypes={['examination']}
              patientId={patientId}
              mode="single"
              displayMode="cards"
              value={
                form.examination_id
                  ? [{ type: 'examination', id: form.examination_id }]
                  : []
              }
              onChange={(sel) =>
                setForm({ ...form, examination_id: sel[0]?.id ?? '' })
              }
              placeholder={t('vaccinations.modal.link_examination_placeholder')}
            />
          </div>
        </div>

        {/* Dose + product details */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pt-2">
          <div className="space-y-2">
            <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1 flex items-center">
              <Hash className="w-3 h-3 mr-2" />
              {t('vaccinations.modal.dose_number')}
            </label>
            <input
              type="text"
              placeholder={t('vaccinations.modal.dose_placeholder')}
              className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-rose-500/20 outline-none"
              value={form.dose_number}
              onChange={(e) => setForm({ ...form, dose_number: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1 flex items-center">
              <Package className="w-3 h-3 mr-2" />
              {t('vaccinations.modal.lot_number')}
            </label>
            <input
              type="text"
              placeholder={t('vaccinations.modal.lot_placeholder')}
              className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-rose-500/20 outline-none"
              value={form.lot_number}
              onChange={(e) => setForm({ ...form, lot_number: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1">
              {t('vaccinations.modal.manufacturer')}
            </label>
            <input
              type="text"
              placeholder={t('vaccinations.modal.manufacturer_placeholder')}
              className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-rose-500/20 outline-none"
              value={form.manufacturer}
              onChange={(e) =>
                setForm({ ...form, manufacturer: e.target.value })
              }
            />
          </div>
          <div className="space-y-2">
            <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1 flex items-center">
              <Building2 className="w-3 h-3 mr-2" />
              {t('vaccinations.modal.location')}
            </label>
            <input
              type="text"
              placeholder={t('vaccinations.modal.location_placeholder')}
              className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-rose-500/20 outline-none"
              value={form.location}
              onChange={(e) => setForm({ ...form, location: e.target.value })}
            />
          </div>
        </div>

        {/* Notes */}
        <div className="space-y-3">
          <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1 flex items-center">
            <Info className="w-3 h-3 mr-2" />
            {t('vaccinations.modal.notes')}
          </label>
          <textarea
            rows={3}
            placeholder={t('vaccinations.modal.notes_placeholder')}
            className="w-full px-4 py-4 bg-gray-50 dark:bg-dark-bg border-none rounded-2xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-rose-500/20 outline-none resize-none"
            value={form.note}
            onChange={(e) => setForm({ ...form, note: e.target.value })}
          />
        </div>

        {/* Graph links on the catalog entry */}
        <LinksSection
          srcType="immunization"
          value={links}
          onChange={setLinks}
          hideWhenEmpty
        />
      </form>

      {showActions && (
        <div className="px-8 py-6 bg-gray-50 dark:bg-dark-bg/50 border-t border-gray-50 dark:border-dark-border flex items-center shrink-0">
          <div className="ml-auto flex items-center space-x-4">
            {onCancel && (
              <button
                type="button"
                onClick={onCancel}
                disabled={loading}
                className="px-6 py-2.5 text-sm font-bold text-gray-500 hover:text-gray-700 dark:text-dark-muted transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {t('common.cancel')}
              </button>
            )}
            <button
              onClick={handleSubmit}
              disabled={!canSubmit}
              className="px-8 py-2.5 bg-rose-600 hover:bg-rose-700 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-xl font-bold text-sm shadow-lg shadow-rose-500/20 transition-all flex items-center space-x-2"
            >
              {loading ? (
                <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              <span>
                {submitLabel ??
                  (immunization
                    ? t('vaccinations.modal.update_record')
                    : t('vaccinations.modal.save'))}
              </span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default VaccinationForm;
