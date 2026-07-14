import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Pill, Plus, Search, ListTree, Filter, LayoutGrid, List } from 'lucide-react';
import { getPatientMedications, MedicationRecord, deletePatientMedication } from '../../services/medicationService';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import MedicationCard from '../../components/medications/MedicationCard';
import { MedicationModal } from '../../components/patients/MedicationModal';
import { LoadingState } from '../../components/ui/LoadingState';
import { NoPatientState } from '../../components/ui/NoPatientState';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { useCreateIntent } from '../../hooks/useCreateIntent';

function MedicationList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { currentPatient } = usePatientStore();
  const showConfirmation = useUIStore(state => state.showConfirmation);
  
  const [medications, setMedications] = useState<MedicationRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const searchTerm = useUIStore(state => state.pageSearchTerm);
  const setSearchTerm = useUIStore(state => state.setPageSearchTerm);
  const setIsPageSearchSupported = useUIStore(state => state.setIsPageSearchSupported);
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');
  
  // Modal states
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedMedication, setSelectedMedication] = useState<MedicationRecord | undefined>(undefined);

  // Open the create modal automatically when arrived via ?new=medication
  useCreateIntent(() => { setSelectedMedication(undefined); setIsModalOpen(true); }, 'medication');

  const fetchMedications = async () => {
    if (!currentPatient?.id) return;
    setLoading(true);
    try {
      const data = await getPatientMedications(currentPatient.id);
      setMedications(data);
    } catch (error) {
      console.error('Failed to fetch patient medications:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMedications();
  }, [currentPatient?.id]);

  useEffect(() => {
    setIsPageSearchSupported(true);
    return () => {
      setIsPageSearchSupported(false);
      setSearchTerm('');
    };
  }, [setIsPageSearchSupported, setSearchTerm]);

  const handleDelete = (med: MedicationRecord) => {
    showConfirmation({
      title: t('medications.remove_record'),
      message: t('medications.remove_record_confirm', { name: med.code.text }),
      confirmLabel: t('common.delete'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deletePatientMedication(med.id);
          fetchMedications();
        } catch (err) {
          console.error("Failed to delete medication", err);
        }
      }
    });
  };

  const filteredMedications = medications.filter(med => {
    const matchesSearch = med.code.text.toLowerCase().includes(searchTerm.toLowerCase()) || 
                         med.reason?.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesStatus = statusFilter === 'all' || med.status?.toLowerCase() === statusFilter.toLowerCase();
    return matchesSearch && matchesStatus;
  });

  const activeMeds = filteredMedications.filter(m => m.status?.toLowerCase() === 'active');
  const otherMeds = filteredMedications.filter(m => m.status?.toLowerCase() !== 'active');

  if (!currentPatient) {
    return <NoPatientState icon={Pill} contextKey="medications" />;
  }

  return (
    <div className="max-w-7xl mx-auto space-y-8 pb-20">
      <PageHeader
        title={t('common.medications')}
        subtitle={t('medications.patient_medications_for', { name: `${currentPatient.name?.given?.join(' ') ?? ''} ${currentPatient.name?.family ?? ''}`.trim() })}
        icon={<Pill className="w-8 h-8" />}
        breadcrumbs={[]}
      />

      <StickyToolbar
        center={
          <div className="flex items-center space-x-3">
             <div className="flex items-center bg-gray-100 dark:bg-dark-bg/50 p-1 rounded-2xl border border-gray-100 dark:border-dark-border">
                {['all', 'active', 'on-hold', 'stopped'].map((s) => (
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
                >
                  <LayoutGrid className="w-4 h-4" />
                </button>
                <button 
                  onClick={() => setViewMode('list')}
                  className={`p-1.5 rounded-xl transition-all ${viewMode === 'list' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-md' : 'text-gray-400'}`}
                >
                  <List className="w-4 h-4" />
                </button>
             </div>
          </div>
        }
        actions={
          <>
            <button
              onClick={() => navigate('/catalogs?type=medication')}
              className="flex items-center space-x-2 px-6 py-2.5 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border text-brand-navy dark:text-dark-text rounded-xl font-bold text-sm hover:bg-gray-50 transition-all shadow-sm active:scale-95"
            >
              <ListTree className="w-5 h-5 text-blue-500" />
              <span className="hidden sm:inline">{t('medications.open_catalog')}</span>
            </button>
            
            <button 
              onClick={() => { setSelectedMedication(undefined); setIsModalOpen(true); }}
              className="flex items-center space-x-2 px-8 py-2.5 bg-blue-600 text-white rounded-xl font-bold text-sm hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none active:scale-95"
            >
              <Plus className="w-5 h-5" />
              <span className="hidden sm:inline">{t('medications.add_drug')}</span>
            </button>
          </>
        }
      />

      {loading ? (
        <LoadingState variant="section" />
      ) : filteredMedications.length > 0 ? (
        <div className="space-y-12">
          {activeMeds.length > 0 && (
            <div className="space-y-6">
              <div className="flex items-center space-x-4">
                <div className="h-px flex-1 bg-gray-100 dark:bg-dark-border"></div>
                <h2 className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.3em] whitespace-nowrap">
                  {t('medications.active_prescriptions')}
                </h2>
                <div className="h-px flex-1 bg-gray-100 dark:bg-dark-border"></div>
              </div>
              <div className={viewMode === 'grid' ? 'grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6' : 'space-y-4'}>
                {activeMeds.map(med => (
                  <MedicationCard 
                    key={med.id} 
                    medication={med}
                    onEdit={() => { setSelectedMedication(med); setIsModalOpen(true); }}
                    onDelete={() => handleDelete(med)}
                    compact={viewMode === 'list'}
                  />
                ))}
              </div>
            </div>
          )}

          {otherMeds.length > 0 && (
            <div className="space-y-6">
              <div className="flex items-center space-x-4">
                <div className="h-px flex-1 bg-gray-100 dark:bg-dark-border"></div>
                <h2 className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.3em] whitespace-nowrap">
                  {t('medications.medication_history')}
                </h2>
                <div className="h-px flex-1 bg-gray-100 dark:bg-dark-border"></div>
              </div>
              <div className={viewMode === 'grid' ? 'grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6' : 'space-y-4'}>
                {otherMeds.map(med => (
                  <MedicationCard 
                    key={med.id} 
                    medication={med}
                    onEdit={() => { setSelectedMedication(med); setIsModalOpen(true); }}
                    onDelete={() => handleDelete(med)}
                    compact={viewMode === 'list'}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="py-32 text-center bg-gray-50/30 dark:bg-dark-bg/20 rounded-[3rem] border-4 border-dashed border-gray-100 dark:border-dark-border">
          <Pill className="w-16 h-16 text-gray-200 mx-auto mb-6" />
          <h4 className="text-lg font-bold text-gray-500">{t('medications.no_medications_found')}</h4>
          <p className="text-gray-400 text-sm mt-2">{t('medications.try_adjusting_filters')}</p>
        </div>
      )}

      <MedicationModal 
        isOpen={isModalOpen} 
        onClose={() => { setIsModalOpen(false); setSelectedMedication(undefined); }} 
        patientId={currentPatient.id}
        medication={selectedMedication}
        onSuccess={fetchMedications}
      />
    </div>
  );
}

export default MedicationList;
