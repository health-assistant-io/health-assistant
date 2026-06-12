import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, Pill, Search, Plus, ChevronRight, Info, Users, X, Save, AlertCircle } from 'lucide-react';
import { useUIStore } from '../../store/slices/uiSlice';
import { searchMedicationCatalog, MedicationCatalogEntry, addCustomMedication } from '../../services/medicationService';
import { AIAssistButton } from '../../components/ui/AIAssistButton';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';

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
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [formData, setFormData] = useState({
    name: '',
    description: '',
    indications: '',
    dosage_info: '',
    contraindications: '',
    side_effects: ''
  });

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
    setFormData({
      name: '',
      description: '',
      indications: '',
      dosage_info: '',
      contraindications: '',
      side_effects: ''
    });
    setError(null);
    setIsModalOpen(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.name.trim()) {
      setError(t('medications.name_required'));
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const payload = {
        ...formData,
        side_effects: formData.side_effects.split(',').map(s => s.trim()).filter(s => s !== '')
      };
      const result = await addCustomMedication(payload);
      setIsModalOpen(false);
      fetchMedications();
    } catch (err: any) {
      setError(err.response?.data?.detail || t('medications.failed_add'));
    } finally {
      setSubmitting(false);
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

      {/* New Medication Modal */}
      {isModalOpen && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-[1000] p-4 animate-in fade-in duration-200">
          <div className="bg-white dark:bg-dark-surface rounded-2xl w-full max-w-lg shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200">
            <div className="px-6 py-4 border-b border-gray-100 dark:border-dark-border flex justify-between items-center bg-white dark:bg-dark-surface sticky top-0 z-10">
              <h2 className="text-xl font-bold text-[#1a2b4b] dark:text-dark-text">
                {t('medications.add_custom_title')}
              </h2>
              <div className="flex items-center space-x-2">
                <AIAssistButton 
                  taskType="define_medication"
                  context={{}}
                  onSuggestedData={(data) => {
                    setFormData(prev => ({
                      ...prev,
                      name: data.name || prev.name,
                      description: data.description || prev.description,
                      indications: data.indications || prev.indications,
                      dosage_info: data.dosage_info || prev.dosage_info,
                      contraindications: data.contraindications || prev.contraindications,
                      side_effects: data.side_effects ? data.side_effects.join(', ') : prev.side_effects
                    }));
                  }}
                />
                <button onClick={() => setIsModalOpen(false)} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-border rounded-full transition-colors">
                  <X className="w-5 h-5 text-gray-500" />
                </button>
              </div>
            </div>
            
            <form onSubmit={handleSubmit} className="p-6 space-y-4 max-h-[70vh] overflow-y-auto custom-scrollbar">
              {error && (
                <div className="p-3 bg-red-50 border border-red-100 text-red-600 text-sm rounded-xl flex items-start space-x-2">
                  <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                  <span>{error}</span>
                </div>
              )}

              <div>
                <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1.5">{t('medications.name')} *</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. Amoxicillin"
                  className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all dark:text-dark-text"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                />
              </div>

              <div>
                <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1.5">{t('medications.description')}</label>
                <textarea
                  placeholder={t('medications.description_placeholder')}
                  rows={3}
                  className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all dark:text-dark-text resize-none"
                  value={formData.description}
                  onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                />
              </div>

              <div>
                <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1.5">{t('medications.indications')}</label>
                <input
                  type="text"
                  placeholder={t('medications.indications_placeholder')}
                  className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all dark:text-dark-text"
                  value={formData.indications}
                  onChange={(e) => setFormData({ ...formData, indications: e.target.value })}
                />
              </div>

              <div>
                <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1.5">{t('medications.dosage_info')}</label>
                <input
                  type="text"
                  placeholder={t('medications.dosage_placeholder')}
                  className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all dark:text-dark-text"
                  value={formData.dosage_info}
                  onChange={(e) => setFormData({ ...formData, dosage_info: e.target.value })}
                />
              </div>

              <div>
                <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1.5">{t('medications.contraindications')}</label>
                <input
                  type="text"
                  placeholder={t('medications.contraindications_placeholder')}
                  className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all dark:text-dark-text"
                  value={formData.contraindications}
                  onChange={(e) => setFormData({ ...formData, contraindications: e.target.value })}
                />
              </div>

              <div>
                <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1.5">{t('medications.side_effects')}</label>
                <input
                  type="text"
                  placeholder={t('medications.side_effects_placeholder')}
                  className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all dark:text-dark-text"
                  value={formData.side_effects}
                  onChange={(e) => setFormData({ ...formData, side_effects: e.target.value })}
                />
              </div>

              <div className="pt-4 flex space-x-3 bg-white dark:bg-dark-surface sticky bottom-0">
                <button
                  type="button"
                  onClick={() => setIsModalOpen(false)}
                  className="flex-1 px-4 py-2.5 border border-gray-200 dark:border-dark-border rounded-xl hover:bg-gray-50 dark:hover:bg-dark-border transition-colors font-bold text-gray-700 dark:text-dark-muted"
                >
                  {t('common.cancel')}
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="flex-1 px-4 py-2.5 bg-blue-600 text-white rounded-xl hover:bg-blue-700 transition-all font-bold flex items-center justify-center space-x-2 shadow-lg shadow-blue-200/50 dark:shadow-none disabled:opacity-50 active:scale-95"
                >
                  {submitting ? (
                    <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin"></div>
                  ) : (
                    <>
                      <Save className="w-4 h-4" />
                      <span>{t('medications.create_medication')}</span>
                    </>
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

export default MedicationCatalog;
