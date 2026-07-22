import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ShieldAlert, Plus, ListTree, LayoutGrid, List } from 'lucide-react';
import {
  getPatientAllergies,
  deletePatientAllergy,
  AllergyIntolerance,
} from '../../services/allergyService';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import AllergyCard from '../../components/allergies/AllergyCard';
import { AllergyModal } from '../../components/patients/AllergyModal';
import { LoadingState } from '../../components/ui/LoadingState';
import { NoPatientState } from '../../components/ui/NoPatientState';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { useCreateIntent } from '../../hooks/useCreateIntent';

/**
 * Patient-scoped allergy list page. Mirrors `MedicationList`: status filter,
 * grid/list view toggle, catalog shortcut, add button, active/resolved
 * sectioning. Replaces the legacy cross-patient `/alerts` page.
 */
function AllergyList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { currentPatient } = usePatientStore();
  const showConfirmation = useUIStore(state => state.showConfirmation);

  const [allergies, setAllergies] = useState<AllergyIntolerance[]>([]);
  const [loading, setLoading] = useState(true);
  const searchTerm = useUIStore(state => state.pageSearchTerm);
  const setSearchTerm = useUIStore(state => state.setPageSearchTerm);
  const setIsPageSearchSupported = useUIStore(state => state.setIsPageSearchSupported);
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');

  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedAllergy, setSelectedAllergy] = useState<AllergyIntolerance | undefined>(undefined);

  // Open the create modal automatically when arrived via ?new=allergy
  useCreateIntent(() => {
    setSelectedAllergy(undefined);
    setIsModalOpen(true);
  }, 'allergy');

  const fetchAllergies = async () => {
    if (!currentPatient?.id) return;
    setLoading(true);
    try {
      const data = await getPatientAllergies(currentPatient.id);
      setAllergies(data);
    } catch (error) {
      console.error('Failed to fetch patient allergies:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAllergies();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentPatient?.id]);

  useEffect(() => {
    setIsPageSearchSupported(true);
    return () => {
      setIsPageSearchSupported(false);
      setSearchTerm('');
    };
  }, [setIsPageSearchSupported, setSearchTerm]);

  const handleDelete = (a: AllergyIntolerance) => {
    showConfirmation({
      title: t('allergies.remove_title'),
      message: t('allergies.remove_confirm', { name: a.code.text }),
      confirmLabel: t('common.delete'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deletePatientAllergy(a.id);
          fetchAllergies();
        } catch (err) {
          console.error('Failed to delete allergy', err);
        }
      },
    });
  };

  const filteredAllergies = allergies.filter(a => {
    const matchesSearch =
      a.code.text.toLowerCase().includes(searchTerm.toLowerCase()) ||
      (a.note ?? '').toLowerCase().includes(searchTerm.toLowerCase()) ||
      (a.category ?? '').toLowerCase().includes(searchTerm.toLowerCase());
    const matchesStatus =
      statusFilter === 'all' || (a.clinical_status ?? '').toLowerCase() === statusFilter.toLowerCase();
    return matchesSearch && matchesStatus;
  });

  const activeAllergies = filteredAllergies.filter(
    a => (a.clinical_status ?? '').toUpperCase() === 'ACTIVE',
  );
  const otherAllergies = filteredAllergies.filter(
    a => (a.clinical_status ?? '').toUpperCase() !== 'ACTIVE',
  );

  if (!currentPatient) {
    return <NoPatientState icon={ShieldAlert} contextKey="allergies" />;
  }

  return (
    <div className="max-w-7xl mx-auto space-y-8 pb-20">
      <PageHeader
        title={t('common.allergies')}
        subtitle={t('allergies.patient_allergies_for', {
          defaultValue: 'Allergies for {{name}}',
          name: `${currentPatient.name?.given?.join(' ') ?? ''} ${currentPatient.name?.family ?? ''}`.trim(),
        })}
        icon={<ShieldAlert className="w-8 h-8" />}
        breadcrumbs={[]}
      />

      <StickyToolbar
        center={
          <div className="flex items-center space-x-3">
            <div className="flex items-center bg-gray-100 dark:bg-dark-bg/50 p-1 rounded-2xl border border-gray-100 dark:border-dark-border">
              {['all', 'active', 'inactive', 'resolved'].map(s => (
                <button
                  key={s}
                  onClick={() => setStatusFilter(s)}
                  className={`px-4 py-1.5 rounded-xl text-[10px] font-black uppercase tracking-widest transition-all ${statusFilter === s ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-md' : 'text-gray-400 hover:text-gray-600'}`}
                >
                  {s}
                </button>
              ))}
            </div>

            <div className="flex items-center bg-gray-100 dark:bg-dark-bg/50 p-1 rounded-2xl border border-gray-100 dark:border-dark-border">
              <button
                onClick={() => setViewMode('grid')}
                className={`p-1.5 rounded-xl transition-all ${viewMode === 'grid' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-md' : 'text-gray-400'}`}
                aria-label="Grid view"
              >
                <LayoutGrid className="w-4 h-4" />
              </button>
              <button
                onClick={() => setViewMode('list')}
                className={`p-1.5 rounded-xl transition-all ${viewMode === 'list' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-md' : 'text-gray-400'}`}
                aria-label="List view"
              >
                <List className="w-4 h-4" />
              </button>
            </div>
          </div>
        }
        actions={
          <>
            <button
              onClick={() => navigate('/catalogs?type=allergy')}
              className="flex items-center space-x-2 px-6 py-2.5 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border text-brand-navy dark:text-dark-text rounded-xl font-bold text-sm hover:bg-gray-50 transition-all shadow-sm active:scale-95"
            >
              <ListTree className="w-5 h-5 text-blue-500" />
              <span className="hidden sm:inline">{t('allergies.open_catalog', 'Open Catalog')}</span>
            </button>
            <button
              onClick={() => {
                setSelectedAllergy(undefined);
                setIsModalOpen(true);
              }}
              className="flex items-center space-x-2 px-8 py-2.5 bg-rose-600 text-white rounded-xl font-bold text-sm hover:bg-rose-700 transition-all shadow-lg shadow-rose-200/50 dark:shadow-none active:scale-95"
            >
              <Plus className="w-5 h-5" />
              <span className="hidden sm:inline">{t('allergies.add_allergy', 'Add Allergy')}</span>
            </button>
          </>
        }
      />

      {loading ? (
        <LoadingState variant="section" />
      ) : filteredAllergies.length > 0 ? (
        <div className="space-y-12">
          {activeAllergies.length > 0 && (
            <div className="space-y-6">
              <div className="flex items-center space-x-4">
                <div className="h-px flex-1 bg-gray-100 dark:bg-dark-border"></div>
                <h2 className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.3em] whitespace-nowrap">
                  {t('allergies.active_allergies', 'Active allergies')}
                </h2>
                <div className="h-px flex-1 bg-gray-100 dark:bg-dark-border"></div>
              </div>
              <div className={viewMode === 'grid' ? 'grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6' : 'space-y-4'}>
                {activeAllergies.map(a => (
                  <AllergyCard
                    key={a.id}
                    allergy={a}
                    onEdit={() => {
                      setSelectedAllergy(a);
                      setIsModalOpen(true);
                    }}
                    onDelete={() => handleDelete(a)}
                    compact={viewMode === 'list'}
                  />
                ))}
              </div>
            </div>
          )}

          {otherAllergies.length > 0 && (
            <div className="space-y-6">
              <div className="flex items-center space-x-4">
                <div className="h-px flex-1 bg-gray-100 dark:bg-dark-border"></div>
                <h2 className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.3em] whitespace-nowrap">
                  {t('allergies.resolved_history', 'Resolved / inactive')}
                </h2>
                <div className="h-px flex-1 bg-gray-100 dark:bg-dark-border"></div>
              </div>
              <div className={viewMode === 'grid' ? 'grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6' : 'space-y-4'}>
                {otherAllergies.map(a => (
                  <AllergyCard
                    key={a.id}
                    allergy={a}
                    onEdit={() => {
                      setSelectedAllergy(a);
                      setIsModalOpen(true);
                    }}
                    onDelete={() => handleDelete(a)}
                    compact={viewMode === 'list'}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="py-32 text-center bg-gray-50/30 dark:bg-dark-bg/20 rounded-[3rem] border-4 border-dashed border-gray-100 dark:border-dark-border">
          <ShieldAlert className="w-16 h-16 text-gray-200 mx-auto mb-6" />
          <h4 className="text-lg font-bold text-gray-500">
            {t('allergies.no_allergies_found', 'No allergies found')}
          </h4>
          <p className="text-gray-400 text-sm mt-2">
            {t('allergies.try_adjusting_filters', 'Try adjusting filters or add a new allergy.')}
          </p>
        </div>
      )}

      <AllergyModal
        isOpen={isModalOpen}
        onClose={() => {
          setIsModalOpen(false);
          setSelectedAllergy(undefined);
        }}
        patientId={currentPatient.id}
        allergy={selectedAllergy}
        onSuccess={fetchAllergies}
      />
    </div>
  );
}

export default AllergyList;
