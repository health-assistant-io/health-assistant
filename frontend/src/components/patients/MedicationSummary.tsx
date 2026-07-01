import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Pill, Calendar, Clock, List } from 'lucide-react';
import { getPatientMedications, MedicationRecord, deletePatientMedication } from '../../services/medicationService';
import { useUIStore } from '../../store/slices/uiSlice';
import { MedicationModal } from './MedicationModal';
import { UniversalCalendar } from '../ui/UniversalCalendar';
import MedicationCard from '../medications/MedicationCard';
import SummaryCardHeader, { TAG_NEUTRAL } from '../ui/SummaryCardHeader';

interface Props {
  patientId: string;
}

export const MedicationSummary: React.FC<Props> = ({ patientId }) => {
  const { t } = useTranslation();
  const [medications, setMedications] = useState<MedicationRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedMedication, setSelectedMedication] = useState<MedicationRecord | undefined>(undefined);
  const [viewMode, setViewMode] = useState<'compact' | 'timeline' | 'calendar'>('compact');
  
  const showConfirmation = useUIStore(state => state.showConfirmation);

  const fetchMedications = async () => {
    try {
      setLoading(true);
      const data = await getPatientMedications(patientId);
      setMedications(data);
    } catch (err) {
      console.error("Failed to fetch medications", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMedications();
  }, [patientId]);

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

  const activeMeds = medications.filter(m => m.status?.toLowerCase() === 'active');
  const pastMeds = medications.filter(m => m.status?.toLowerCase() !== 'active');

  if (loading) return (
    <div className="animate-pulse bg-white dark:bg-dark-surface rounded-2xl p-6 border border-gray-100 dark:border-dark-border w-full h-full">
      <div className="h-4 w-32 bg-gray-200 rounded mb-4"></div>
      <div className="space-y-3">
        <div className="h-12 bg-gray-50 rounded-xl"></div>
        <div className="h-12 bg-gray-50 rounded-xl"></div>
      </div>
    </div>
  );

  return (
    <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden w-full h-full">
      <SummaryCardHeader
        icon={Pill}
        iconClassName="text-blue-500"
        title={t('common.medications')}
        info={{
          title: t('common.medications'),
          content: t('medications.info_text'),
          ariaLabel: t('common.info'),
        }}
        tags={[
          <span key="active" className={TAG_NEUTRAL}>{activeMeds.length} {t('medications.active')}</span>,
        ]}
        onAdd={() => { setSelectedMedication(undefined); setIsModalOpen(true); }}
        addLabel={t('medications.add_drug')}
        titleTo="/medications"
      />

      <div className="p-6 max-h-[500px] overflow-y-auto custom-scrollbar">
        {medications.length === 0 ? (
          <div className="text-center py-8">
            <Pill className="w-12 h-12 text-gray-200 mx-auto mb-3" />
            <p className="text-gray-400 text-sm italic">{t('medications.no_patients_prescribed')}</p>
          </div>
        ) : (
          <>
            {/* View-mode toggle — moved from header to body */}
            <div className="flex items-center justify-end mb-4">
              <div className="flex bg-gray-100 dark:bg-dark-bg p-0.5 rounded-lg">
                <button
                  onClick={() => setViewMode('compact')}
                  className={`p-1.5 rounded-md transition-all ${viewMode === 'compact' ? 'bg-white dark:bg-dark-surface shadow-sm text-blue-600' : 'text-gray-400 dark:text-dark-muted hover:text-gray-600'}`}
                  title={t('allergies.compact_view')}
                >
                  <List className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => setViewMode('timeline')}
                  className={`p-1.5 rounded-md transition-all ${viewMode === 'timeline' ? 'bg-white dark:bg-dark-surface shadow-sm text-blue-600' : 'text-gray-400 dark:text-dark-muted hover:text-gray-600'}`}
                  title={t('allergies.timeline_view')}
                >
                  <Clock className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => setViewMode('calendar')}
                  className={`p-1.5 rounded-md transition-all ${viewMode === 'calendar' ? 'bg-white dark:bg-dark-surface shadow-sm text-blue-600' : 'text-gray-400 dark:text-dark-muted hover:text-gray-600'}`}
                  title={t('dashboard.cards.unified_schedule')}
                >
                  <Calendar className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>

            {viewMode === 'compact' ? (
              <div className="space-y-4">
                {activeMeds.length > 0 && (
                  <div className="space-y-3">
                     <h3 className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em] px-1">{t('medications.active_prescriptions')}</h3>
                     <div className="grid grid-cols-1 gap-4">
                       {activeMeds.map(med => (
                        <MedicationCard 
                          key={med.id} 
                          medication={med} 
                          onEdit={() => { setSelectedMedication(med); setIsModalOpen(true); }}
                          onDelete={() => handleDelete(med)}
                          compact={true}
                        />
                      ))}
                   </div>
                </div>
              )}

              {pastMeds.length > 0 && (
                <div className={`${activeMeds.length > 0 ? 'pt-8 mt-8 border-t' : ''} border-gray-100 dark:border-dark-border space-y-3`}>
                   <h3 className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em] px-1">{t('medications.medication_history')}</h3>
                   <div className="grid grid-cols-1 gap-4 opacity-75 grayscale-[0.5] hover:grayscale-0 transition-all">
                      {pastMeds.map(med => (
                        <MedicationCard 
                          key={med.id} 
                          medication={med} 
                          onEdit={() => { setSelectedMedication(med); setIsModalOpen(true); }}
                          onDelete={() => handleDelete(med)}
                          compact={true}
                        />
                      ))}
                   </div>
                </div>
              )}
             </div>
           ) : viewMode === 'timeline' ? (
             <MedicationTimeline
               medications={medications}
               t={t}
               onEdit={(med) => { setSelectedMedication(med); setIsModalOpen(true); }}
             />
           ) : (
             <UniversalCalendar config={{ patientId, types: ['medication'] }} defaultView="classic" />
           )}
           </>
         )}
      </div>

      <MedicationModal 
        isOpen={isModalOpen} 
        onClose={() => { setIsModalOpen(false); setSelectedMedication(undefined); }} 
        patientId={patientId}
        medication={selectedMedication}
        onSuccess={fetchMedications}
      />
    </div>
  );
};

const MedicationTimeline: React.FC<{
  medications: MedicationRecord[];
  t: any;
  onEdit: (med: MedicationRecord) => void;
}> = ({ medications, t, onEdit }) => {
  const sorted = [...medications].sort((a, b) => {
    const dateA = new Date(a.start_date || 0).getTime();
    const dateB = new Date(b.start_date || 0).getTime();
    return dateB - dateA;
  });

  return (
    <div className="space-y-8 relative before:absolute before:left-4 before:top-2 before:bottom-2 before:w-0.5 before:bg-gray-100 dark:before:bg-dark-border">
      {sorted.map(med => (
        <div key={med.id} className="relative pl-12 group">
          <div className={`absolute left-[-0.15rem] top-1.5 w-3.5 h-3.5 rounded-full border-2 bg-white dark:bg-dark-surface transition-colors z-10 ${med.status?.toLowerCase() === 'active' ? 'border-blue-500 shadow-[0_0_8px_rgba(59,130,246,0.5)]' : 'border-gray-300 dark:border-dark-border'}`} />
          
          <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-2">
            <span className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">
              {med.start_date ? new Date(med.start_date).toLocaleDateString(undefined, { month: 'short', year: 'numeric' }) : t('patients.unknown')} 
              {med.end_date && ` — ${new Date(med.end_date).toLocaleDateString(undefined, { month: 'short', year: 'numeric' })}`}
              {med.status?.toLowerCase() === 'active' && !med.end_date && ` — ${t('common.today')}`}
            </span>
            <span className={`text-[10px] font-bold uppercase tracking-tighter px-2 py-0.5 rounded ${med.status?.toLowerCase() === 'active' ? 'text-blue-600 bg-blue-50 dark:bg-blue-900/20' : 'text-gray-400 bg-gray-50 dark:bg-dark-bg'}`}>
                {med.status}
            </span>
          </div>

          <div 
            className="p-5 bg-gray-50/50 dark:bg-dark-bg/30 rounded-2xl border border-gray-100 dark:border-dark-border cursor-pointer hover:border-blue-200 dark:hover:border-blue-900 transition-all"
            onClick={() => onEdit(med)}
          >
            <div className="flex justify-between items-start">
              <div>
                <h4 className="font-bold text-gray-900 dark:text-dark-text text-lg">{med.code.text}</h4>
                <p className="text-sm text-gray-500 dark:text-dark-muted font-medium mt-1">{med.dosage} • {med.frequency?.display || t('medications.no_dosage')}</p>
              </div>
              {med.reason && (
                <div className="text-right">
                    <p className="text-[10px] text-gray-400 dark:text-dark-muted font-bold uppercase tracking-widest">{t('biomarkers.clinical_significance')}</p>
                    <p className="text-xs font-bold text-gray-700 dark:text-dark-text mt-1">{med.reason}</p>
                </div>
              )}
            </div>
            {med.note && (
                <p className="mt-3 text-sm text-gray-500 dark:text-dark-muted italic border-l-2 border-gray-200 dark:border-dark-border pl-3">
                    {med.note}
                </p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
};
