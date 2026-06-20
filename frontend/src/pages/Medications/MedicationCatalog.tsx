import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Pill, Plus, ChevronRight, Info, Users, AlertCircle } from 'lucide-react';
import { useUIStore } from '../../store/slices/uiSlice';
import { searchMedicationCatalog, MedicationCatalogEntry, addCustomMedication } from '../../services/medicationService';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import {
  MedicationDefinitionForm,
  MedicationDefinitionFormPayload,
} from '../../components/patients/MedicationDefinitionForm';

function MedicationCatalog() {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const [medications, setMedications] = useState<MedicationCatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const searchTerm = useUIStore(state => state.pageSearchTerm);
  const setSearchTerm = useUIStore(state => state.setPageSearchTerm);
  const setIsPageSearchSupported = useUIStore(state => state.setIsPageSearchSupported);

  // Modal states
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [modalError, setModalError] = useState<string | null>(null);

  const fetchMedications = async () => {
    setLoading(true);
    try {
      const data = await searchMedicationCatalog(searchTerm);
      setMedications(data);
    } catch (error) {
      console.error('Failed to fetch medications:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const timer = setTimeout(() => {
      fetchMedications();
    }, 300);
    return () => clearTimeout(timer);
  }, [searchTerm]);

  useEffect(() => {
    setIsPageSearchSupported(true);
    return () => {
      setIsPageSearchSupported(false);
      setSearchTerm('');
    };
  }, [setIsPageSearchSupported, setSearchTerm]);

  const handleOpenModal = () => {
    setModalError(null);
    setIsModalOpen(true);
  };

  const handleSubmit = async (payload: MedicationDefinitionFormPayload) => {
    try {
      setModalError(null);
      await addCustomMedication(payload);
      setIsModalOpen(false);
      fetchMedications();
    } catch (err: any) {
      const msg = err.response?.data?.detail || t('medications.failed_add');
      setModalError(typeof msg === 'string' ? msg : JSON.stringify(msg));
      throw err; // keep the form's loading state reset
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('medications.catalog_title')}
        subtitle={t('medications.catalog_subtitle')}
        icon={<Pill className="w-8 h-8" />}
        breadcrumbs={[
          { label: t('common.medications'), path: '/medications' }
        ]}
        showBackButton={true}
      />

      <StickyToolbar
        actions={
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
            <button 
              className="flex items-center justify-center space-x-2 px-6 py-2.5 bg-blue-600 text-white rounded-xl hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none font-bold active:scale-95 whitespace-nowrap"
              onClick={handleOpenModal}
            >
              <Plus className="w-4 h-4" />
              <span>{t('medications.new_medication')}</span>
            </button>
          </div>
        }
      />

      {loading ? (
        <div className="flex flex-col items-center justify-center py-20">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mb-4"></div>
          <p className="text-gray-500 animate-pulse">{t('medications.loading_medications')}</p>
        </div>
      ) : medications.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 sm:gap-6">
          {medications.map((med) => (
            <div 
              key={med.id} 
              className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden hover:shadow-md transition-all group cursor-pointer"
              onClick={() => navigate(`/medications/details/${med.id}`)}
            >
              <div className="p-3.5 sm:p-6">
                <div className="flex justify-between items-start mb-4">
                  <div className="w-12 h-12 bg-blue-50 dark:bg-blue-900/20 rounded-xl flex items-center justify-center border border-blue-100 dark:border-blue-800">
                    <Pill className="w-6 h-6 text-blue-600 dark:text-blue-400" />
                  </div>
                  {med.is_custom && (
                    <span className="px-2 py-1 text-[10px] font-bold uppercase bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 rounded">
                      {t('medications.custom_resource')}
                    </span>
                  )}
                </div>

                <h3 className="text-xl font-bold text-[#1a2b4b] dark:text-dark-text group-hover:text-blue-600 transition-colors truncate">
                  {med.name}
                </h3>
                
                <p className="text-sm text-gray-500 dark:text-dark-muted mt-2 line-clamp-2 min-h-[40px]">
                  {med.description || t('medications.no_description')}
                </p>

                <div className="mt-6 flex flex-wrap gap-2">
                  {med.indications && (
                    <div className="flex items-center text-[11px] font-medium text-green-700 dark:text-green-400 bg-green-50 dark:bg-green-900/20 px-2 py-1 rounded">
                      <Info className="w-3 h-3 mr-1" />
                      <span className="truncate max-w-[120px]">{t('medications.indications')}</span>
                    </div>
                  )}
                  {med.side_effects && med.side_effects.length > 0 && (
                    <div className="flex items-center text-[11px] font-medium text-red-700 dark:text-red-400 bg-red-50 dark:bg-red-900/20 px-2 py-1 rounded">
                      <Users className="w-3 h-3 mr-1" />
                      <span>{med.side_effects.length} {t('medications.side_effects')}</span>
                    </div>
                  )}
                </div>
              </div>

              <div className="px-3.5 sm:px-6 py-2.5 sm:py-4 bg-gray-50/50 dark:bg-dark-border/20 border-t border-gray-100 dark:border-dark-border flex items-center justify-between">
                <span className="text-xs font-bold text-blue-600 dark:text-blue-400 uppercase tracking-wider flex items-center group-hover:underline">
                  {t('common.details')}
                  <ChevronRight className="w-3 h-3 ml-1" />
                </span>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center py-20 bg-gray-50 dark:bg-dark-bg/30 rounded-3xl border-2 border-dashed border-gray-200 dark:border-dark-border">
          <div className="w-16 h-16 bg-white dark:bg-dark-surface rounded-full flex items-center justify-center shadow-sm mb-4">
            <Pill className="w-8 h-8 text-gray-400" />
          </div>
          <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">{t('medications.no_medications_found')}</h3>
          <p className="text-gray-500 mt-1 mb-6 text-center max-w-xs">
            {searchTerm ? t('medications.no_results_for', { term: searchTerm }) : t('medications.catalog_empty')}
          </p>
        </div>
      )}

      {/* New Medication Modal — hosts the headless MedicationDefinitionForm. */}
      {isModalOpen && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-[1000] p-4 animate-in fade-in duration-200">
          <div className="bg-white dark:bg-dark-surface rounded-2xl w-full max-w-lg shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200 flex flex-col max-h-[90vh]">
            {modalError && (
              <div className="mx-6 mt-4 p-3 bg-rose-50 dark:bg-rose-900/10 border border-rose-200 dark:border-rose-500/30 text-rose-700 dark:text-rose-300 text-sm rounded-xl flex items-start space-x-2">
                <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                <span className="break-words">{modalError}</span>
              </div>
            )}
            <MedicationDefinitionForm
              onSubmit={handleSubmit}
              onCancel={() => setIsModalOpen(false)}
            />
          </div>
        </div>
      )}
    </div>
  );
}

export default MedicationCatalog;
