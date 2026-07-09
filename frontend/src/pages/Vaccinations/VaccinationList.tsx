/**
 * Patient vaccinations page — mirrors MedicationList for immunizations.
 *
 * Lists a patient's immunization records (from `/vaccines/patient/{id}`) with
 * add (via a vaccine-catalog picker modal) and delete. Route: `/vaccinations`.
 */
import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Syringe, Plus, Trash2, X, Search, Calendar, Hash } from 'lucide-react';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import { LoadingState } from '../../components/ui/LoadingState';
import { NoPatientState } from '../../components/ui/NoPatientState';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import {
  getPatientImmunizations,
  addPatientImmunization,
  deletePatientImmunization,
  searchVaccineCatalog,
} from '../../services/vaccineService';
import type {
  PatientImmunization,
  VaccineCatalogEntry,
} from '../../types/vaccine';

export function VaccinationList() {
  const { t } = useTranslation();
  const { currentPatient } = usePatientStore();
  const showConfirmation = useUIStore((s) => s.showConfirmation);
  const searchTerm = useUIStore((s) => s.pageSearchTerm);
  const setSearchTerm = useUIStore((s) => s.setPageSearchTerm);
  const setIsPageSearchSupported = useUIStore(
    (s) => s.setIsPageSearchSupported,
  );

  const [immunizations, setImmunizations] = useState<PatientImmunization[]>([]);
  const [loading, setLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);

  // Add-modal state
  const [catalog, setCatalog] = useState<VaccineCatalogEntry[]>([]);
  const [catalogSearch, setCatalogSearch] = useState('');
  const [selectedVaccine, setSelectedVaccine] =
    useState<VaccineCatalogEntry | null>(null);
  const [administeredAt, setAdministeredAt] = useState('');
  const [doseNumber, setDoseNumber] = useState('');
  const [lotNumber, setLotNumber] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const fetch = useCallback(async () => {
    if (!currentPatient?.id) return;
    setLoading(true);
    try {
      const data = await getPatientImmunizations(currentPatient.id);
      setImmunizations(data);
    } catch (err) {
      console.error('Failed to fetch immunizations:', err);
    } finally {
      setLoading(false);
    }
  }, [currentPatient?.id]);

  useEffect(() => {
    fetch();
  }, [fetch]);

  useEffect(() => {
    setIsPageSearchSupported(true);
    return () => {
      setIsPageSearchSupported(false);
      setSearchTerm('');
    };
  }, [setIsPageSearchSupported, setSearchTerm]);

  const loadCatalog = useCallback(async () => {
    try {
      const data = await searchVaccineCatalog(catalogSearch || undefined);
      setCatalog(data);
    } catch {
      setCatalog([]);
    }
  }, [catalogSearch]);

  useEffect(() => {
    if (isModalOpen) {
      const timer = setTimeout(() => loadCatalog(), 200);
      return () => clearTimeout(timer);
    }
  }, [isModalOpen, loadCatalog]);

  const handleAdd = async () => {
    if (!currentPatient?.id || !selectedVaccine) return;
    setSubmitting(true);
    try {
      await addPatientImmunization(currentPatient.id, {
        vaccine_catalog_id: selectedVaccine.id,
        vaccine_code: { text: selectedVaccine.name, catalog_id: selectedVaccine.id },
        administered_at: administeredAt
          ? new Date(administeredAt).toISOString()
          : null,
        dose_number: doseNumber || null,
        lot_number: lotNumber || null,
      });
      setIsModalOpen(false);
      setSelectedVaccine(null);
      setAdministeredAt('');
      setDoseNumber('');
      setLotNumber('');
      fetch();
    } catch (err) {
      console.error('Failed to add immunization:', err);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = (imm: PatientImmunization) => {
    showConfirmation({
      title: 'Remove vaccination',
      message: `Remove "${imm.vaccine_code.text}" from this patient's record?`,
      confirmLabel: t('common.delete'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        await deletePatientImmunization(imm.id);
        fetch();
      },
    });
  };

  const filtered = immunizations.filter((imm) =>
    imm.vaccine_code.text.toLowerCase().includes(searchTerm.toLowerCase()),
  );

  if (!currentPatient) {
    return <NoPatientState icon={Syringe} contextKey="vaccinations" />;
  }

  return (
    <div className="max-w-5xl mx-auto pb-20">
      <PageHeader
        title={t('common.vaccinations')}
        subtitle="Patient immunization records"
        icon={<Syringe className="w-8 h-8" />}
        breadcrumbs={[{ label: t('common.patient_record') }]}
        showBackButton
      />

      <StickyToolbar
        actions={
          <div className="flex items-center gap-3">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                placeholder="Search vaccinations…"
                className="w-56 pl-9 pr-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 focus:ring-2 focus:ring-blue-500 outline-none"
              />
            </div>
            <button
              onClick={() => setIsModalOpen(true)}
              className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700"
            >
              <Plus className="w-4 h-4" /> Add
            </button>
          </div>
        }
      />

      {loading ? (
        <LoadingState variant="section" />
      ) : filtered.length === 0 ? (
        <p className="text-center text-gray-500 dark:text-gray-400 py-12">
          No vaccination records found.
        </p>
      ) : (
        <ul className="space-y-3">
          {filtered.map((imm) => (
            <li
              key={imm.id}
              className="flex items-start gap-4 rounded-xl border border-gray-200 dark:border-gray-700 p-4 hover:bg-gray-50 dark:hover:bg-gray-800/50"
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-rose-50 dark:bg-rose-900/20 shrink-0">
                <Syringe className="w-5 h-5 text-rose-500" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="font-semibold text-sm">
                  {imm.vaccine_code.text}
                </p>
                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500 dark:text-gray-400">
                  {imm.administered_at && (
                    <span className="flex items-center gap-1">
                      <Calendar className="w-3 h-3" />
                      {new Date(imm.administered_at).toLocaleDateString()}
                    </span>
                  )}
                  {imm.dose_number && (
                    <span className="flex items-center gap-1">
                      <Hash className="w-3 h-3" />
                      Dose {imm.dose_number}
                    </span>
                  )}
                  {imm.lot_number && <span>Lot: {imm.lot_number}</span>}
                  {imm.status && (
                    <span className="capitalize">{imm.status.replace(/-/g, ' ')}</span>
                  )}
                </div>
                {imm.note && (
                  <p className="mt-1 text-xs text-gray-400">{imm.note}</p>
                )}
              </div>
              <button
                onClick={() => handleDelete(imm)}
                className="p-2 text-gray-400 hover:text-red-500 rounded-lg"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* Add modal */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-lg rounded-xl bg-white dark:bg-gray-800 shadow-xl p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-semibold">Add Vaccination</h3>
              <button onClick={() => setIsModalOpen(false)}>
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Vaccine picker */}
            <div>
              <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
                Vaccine
              </label>
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                <input
                  value={catalogSearch}
                  onChange={(e) => setCatalogSearch(e.target.value)}
                  placeholder="Search vaccine catalog…"
                  className="w-full pl-9 pr-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900"
                />
              </div>
              {selectedVaccine ? (
                <div className="mt-2 flex items-center justify-between rounded-lg bg-blue-50 dark:bg-blue-900/20 px-3 py-2">
                  <span className="text-sm font-medium">
                    {selectedVaccine.name}
                  </span>
                  <button
                    onClick={() => setSelectedVaccine(null)}
                    className="text-xs text-gray-400 hover:text-red-500"
                  >
                    Change
                  </button>
                </div>
              ) : (
                <ul className="mt-2 max-h-40 overflow-auto rounded-lg border border-gray-200 dark:border-gray-700 divide-y divide-gray-100 dark:divide-gray-700">
                  {catalog.map((v) => (
                    <li key={v.id}>
                      <button
                        onClick={() => setSelectedVaccine(v)}
                        className="flex w-full items-center px-3 py-2 text-left text-sm hover:bg-gray-50 dark:hover:bg-gray-800"
                      >
                        <span className="font-medium">{v.name}</span>
                        {v.code && (
                          <span className="ml-auto text-xs text-gray-400">
                            CVX {v.code}
                          </span>
                        )}
                      </button>
                    </li>
                  ))}
                  {catalog.length === 0 && (
                    <li className="px-3 py-4 text-center text-xs text-gray-400">
                      No vaccines found.
                    </li>
                  )}
                </ul>
              )}
            </div>

            {/* Date + dose + lot */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
                  Date
                </label>
                <input
                  type="date"
                  value={administeredAt}
                  onChange={(e) => setAdministeredAt(e.target.value)}
                  className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
                  Dose number
                </label>
                <input
                  value={doseNumber}
                  onChange={(e) => setDoseNumber(e.target.value)}
                  placeholder="e.g. 1, 2, booster"
                  className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm"
                />
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
                  Lot number
                </label>
                <input
                  value={lotNumber}
                  onChange={(e) => setLotNumber(e.target.value)}
                  className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm"
                />
              </div>
            </div>

            <div className="flex justify-end gap-2">
              <button
                onClick={() => setIsModalOpen(false)}
                className="px-4 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700"
              >
                Cancel
              </button>
              <button
                onClick={handleAdd}
                disabled={!selectedVaccine || submitting}
                className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {submitting ? 'Saving…' : 'Add vaccination'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default VaccinationList;
