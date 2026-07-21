/**
 * Patient vaccinations page — mirrors MedicationList for immunizations.
 *
 * Lists a patient's immunization records (`/vaccines/patient/{id}`) with
 * status filtering, grid/list views, completed-vs-other grouping, add/edit via
 * the VaccinationModal (catalog picker + examination instance link), and a
 * shortcut to the vaccine catalog workspace. Route: `/vaccinations`.
 */
import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Syringe, Plus, ListTree, LayoutGrid, List } from 'lucide-react';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import { LoadingState } from '../../components/ui/LoadingState';
import { NoPatientState } from '../../components/ui/NoPatientState';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { useCreateIntent } from '../../hooks/useCreateIntent';
import {
  getPatientImmunizations,
  deletePatientImmunization,
} from '../../services/vaccineService';
import type { PatientImmunization } from '../../types/vaccine';
import { VaccinationCard } from '../../components/vaccinations/VaccinationCard';
import { VaccinationModal } from '../../components/vaccinations/VaccinationModal';

const STATUS_FILTERS = ['all', 'completed', 'not-done', 'entered-in-error'];

export function VaccinationList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { currentPatient } = usePatientStore();
  const showConfirmation = useUIStore((s) => s.showConfirmation);
  const searchTerm = useUIStore((s) => s.pageSearchTerm);
  const setSearchTerm = useUIStore((s) => s.setPageSearchTerm);
  const setIsPageSearchSupported = useUIStore(
    (s) => s.setIsPageSearchSupported,
  );

  const [immunizations, setImmunizations] = useState<PatientImmunization[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');

  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selected, setSelected] = useState<PatientImmunization | undefined>(
    undefined,
  );

  // ?new=vaccination → auto-open the create modal
  useCreateIntent(() => {
    setSelected(undefined);
    setIsModalOpen(true);
  }, 'vaccination');

  const fetchImmunizations = async () => {
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
  };

  useEffect(() => {
    fetchImmunizations();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentPatient?.id]);

  useEffect(() => {
    setIsPageSearchSupported(true);
    return () => {
      setIsPageSearchSupported(false);
      setSearchTerm('');
    };
  }, [setIsPageSearchSupported, setSearchTerm]);

  const handleDelete = (imm: PatientImmunization) => {
    showConfirmation({
      title: t('vaccinations.remove_record'),
      message: t('vaccinations.remove_record_confirm', {
        name: imm.vaccine_code.text,
      }),
      confirmLabel: t('common.delete'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deletePatientImmunization(imm.id);
          fetchImmunizations();
        } catch (err) {
          console.error('Failed to delete immunization', err);
        }
      },
    });
  };

  const filtered = immunizations.filter((imm) => {
    const matchesSearch =
      imm.vaccine_code?.text
        ?.toLowerCase()
        .includes(searchTerm.toLowerCase()) ||
      imm.manufacturer?.toLowerCase().includes(searchTerm.toLowerCase()) ||
      imm.lot_number?.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesStatus =
      statusFilter === 'all' || imm.status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  const completed = filtered.filter((imm) => imm.status === 'completed');
  const others = filtered.filter((imm) => imm.status !== 'completed');

  if (!currentPatient) {
    return <NoPatientState icon={Syringe} contextKey="vaccinations" />;
  }

  const patientName =
    `${currentPatient.name?.given?.join(' ') ?? ''} ${currentPatient.name?.family ?? ''}`.trim();

  const renderSection = (
    title: string,
    items: PatientImmunization[],
  ) => (
    <div className="space-y-6">
      <div className="flex items-center space-x-4">
        <div className="h-px flex-1 bg-gray-100 dark:bg-dark-border" />
        <h2 className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.3em] whitespace-nowrap">
          {title}
        </h2>
        <div className="h-px flex-1 bg-gray-100 dark:bg-dark-border" />
      </div>
      <div
        className={
          viewMode === 'grid'
            ? 'grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6'
            : 'space-y-4'
        }
      >
        {items.map((imm) => (
          <VaccinationCard
            key={imm.id}
            immunization={imm}
            onEdit={(i: PatientImmunization) => {
              setSelected(i);
              setIsModalOpen(true);
            }}
            onDelete={handleDelete}
            compact={viewMode === 'list'}
          />
        ))}
      </div>
    </div>
  );

  return (
    <div className="max-w-7xl mx-auto space-y-8 pb-20">
      <PageHeader
        title={t('common.vaccinations')}
        subtitle={t('vaccinations.patient_vaccinations_for', { name: patientName })}
        icon={<Syringe className="w-8 h-8" />}
        breadcrumbs={[]}
      />

      <StickyToolbar
        center={
          <div className="flex items-center space-x-3">
            <div className="flex items-center bg-gray-100 dark:bg-dark-bg/50 p-1 rounded-2xl border border-gray-100 dark:border-dark-border">
              {STATUS_FILTERS.map((s) => (
                <button
                  key={s}
                  onClick={() => setStatusFilter(s)}
                  className={`px-4 py-1.5 rounded-xl text-[10px] font-black uppercase tracking-widest transition-all ${
                    statusFilter === s
                      ? 'bg-white dark:bg-dark-surface text-rose-600 shadow-md'
                      : 'text-gray-400 hover:text-gray-600'
                  }`}
                >
                  {t(`vaccinations.filter_${s.replace(/-/g, '_')}`, s)}
                </button>
              ))}
            </div>

            <div className="flex items-center bg-gray-100 dark:bg-dark-bg/50 p-1 rounded-2xl border border-gray-100 dark:border-dark-border">
              <button
                onClick={() => setViewMode('grid')}
                className={`p-1.5 rounded-xl transition-all ${
                  viewMode === 'grid'
                    ? 'bg-white dark:bg-dark-surface text-rose-600 shadow-md'
                    : 'text-gray-400'
                }`}
              >
                <LayoutGrid className="w-4 h-4" />
              </button>
              <button
                onClick={() => setViewMode('list')}
                className={`p-1.5 rounded-xl transition-all ${
                  viewMode === 'list'
                    ? 'bg-white dark:bg-dark-surface text-rose-600 shadow-md'
                    : 'text-gray-400'
                }`}
              >
                <List className="w-4 h-4" />
              </button>
            </div>
          </div>
        }
        actions={
          <>
            <button
              onClick={() => navigate('/catalogs?type=vaccine')}
              className="flex items-center space-x-2 px-6 py-2.5 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border text-brand-navy dark:text-dark-text rounded-xl font-bold text-sm hover:bg-gray-50 transition-all shadow-sm active:scale-95"
            >
              <ListTree className="w-5 h-5 text-rose-500" />
              <span className="hidden sm:inline">
                {t('vaccinations.open_catalog')}
              </span>
            </button>
            <button
              onClick={() => {
                setSelected(undefined);
                setIsModalOpen(true);
              }}
              className="flex items-center space-x-2 px-8 py-2.5 bg-rose-600 text-white rounded-xl font-bold text-sm hover:bg-rose-700 transition-all shadow-lg shadow-rose-200/50 dark:shadow-none active:scale-95"
            >
              <Plus className="w-5 h-5" />
              <span className="hidden sm:inline">
                {t('vaccinations.add_vaccine')}
              </span>
            </button>
          </>
        }
      />

      {loading ? (
        <LoadingState variant="section" />
      ) : filtered.length > 0 ? (
        <div className="space-y-12">
          {completed.length > 0 &&
            renderSection(t('vaccinations.immunization_history'), completed)}
          {others.length > 0 &&
            renderSection(t('vaccinations.other_records'), others)}
        </div>
      ) : (
        <div className="py-32 text-center bg-gray-50/30 dark:bg-dark-bg/20 rounded-[3rem] border-4 border-dashed border-gray-100 dark:border-dark-border">
          <Syringe className="w-16 h-16 text-gray-200 mx-auto mb-6" />
          <h4 className="text-lg font-bold text-gray-500">
            {t('vaccinations.no_records_found')}
          </h4>
          <p className="text-gray-400 text-sm mt-2">
            {t('vaccinations.try_adjusting_filters')}
          </p>
        </div>
      )}

      <VaccinationModal
        isOpen={isModalOpen}
        onClose={() => {
          setIsModalOpen(false);
          setSelected(undefined);
        }}
        patientId={currentPatient.id}
        immunization={selected}
        onSuccess={fetchImmunizations}
      />
    </div>
  );
}

export default VaccinationList;
