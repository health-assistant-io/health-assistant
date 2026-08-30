import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { getPatient, deletePatient } from '../../services/patientService';
import { Patient } from '../../types/patient';
import { Edit2, Trash2, Fingerprint, User } from 'lucide-react';
import { useUIStore } from '../../store/slices/uiSlice';
import { usePatientStore } from '../../store/slices/patientSlice';
import { AllergySummary } from '../../components/patients/AllergySummary';
import { MedicationSummary } from '../../components/patients/MedicationSummary';
import { PatientFormWizard } from '../../components/patients/PatientFormWizard';
import BiomarkerSummary from '../../components/patients/BiomarkerSummary';
import ExaminationSummary from '../../components/patients/ExaminationSummary';
import ClinicalEventSummary from '../../components/patients/ClinicalEventSummary';
import ScheduleSummary from '../../components/patients/ScheduleSummary';
import { formatAge } from '../../utils/dateUtils';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { DatePicker } from '../../components/ui/DatePicker';
import { FormModal } from '../../components/ui/FormModal';
import { SetupChecklistCard } from '../../components/setup/SetupChecklistCard';

function PatientDetail() {
  const { t } = useTranslation();
  const { patientId } = useParams<{ patientId: string }>();
  const navigate = useNavigate();
  const showConfirmation = useUIStore(state => state.showConfirmation);
  const { currentPatient, setCurrentPatient } = usePatientStore();
  const [patient, setPatient] = useState<Patient | null>(null);
  const [loading, setLoading] = useState(true);

  // Sync global patient when this patient is loaded
  useEffect(() => {
    if (patient && (!currentPatient || currentPatient.id !== patient.id)) {
      setCurrentPatient(patient);
    }
  }, [patient, currentPatient, setCurrentPatient]);

  // Edit state
  const [isModalOpen, setIsModalOpen] = useState(false);

  useEffect(() => {
    if (patientId) {
      loadData();
    }
  }, [patientId]);

  const loadData = async () => {
    try {
      setLoading(true);
      const patientData = await getPatient(patientId!);
      setPatient(patientData);
    } catch (error) {
      console.error('Failed to load patient details:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleOpenEditModal = () => {
    setIsModalOpen(true);
  };

  const handleDeletePatient = () => {
    if (!patient) return;
    const fullName = `${patient.name?.given?.join(' ')} ${patient.name?.family}`;
    showConfirmation({
      title: t('patients.delete_profile_title'),
      message: t('patients.delete_profile_confirm', { name: fullName }),
      confirmLabel: t('patients.delete_permanently'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deletePatient(patientId!);
          navigate('/patients');
        } catch (err) {
          console.error('Failed to delete patient:', err);
          alert(t('patients.failed_delete'));
        }
      }
    });
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  if (!patient) {
    return <div className="text-center py-10 text-gray-500">{t('patients.patient_not_found')}</div>;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={`${patient.name?.given?.join(' ') ?? ''} ${patient.name?.family ?? ''}`.trim()}
        subtitle={
          <div className="flex items-center">
            <Fingerprint className="w-3 h-3 mr-1" />
            {t('patients.patient_id')}: {patient.id}
          </div>
        }
        icon={<User className="size-5" />}
        breadcrumbs={[
          { label: t('patients.directory'), href: '/patients' }
        ]}
        showBackButton={true}
      />

      <StickyToolbar
        actions={
          <>
            <button 
              onClick={handleOpenEditModal}
              className="flex items-center space-x-2 px-4 py-2.5 border border-gray-200 dark:border-dark-border text-gray-700 dark:text-dark-text rounded-xl hover:bg-gray-50 dark:hover:bg-dark-surface transition-all font-bold active:scale-95"
            >
              <Edit2 className="w-4 h-4" />
              <span>{t('patients.edit_profile')}</span>
            </button>
            <button 
              onClick={handleDeletePatient}
              className="p-2.5 text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-xl transition-colors border border-transparent hover:border-red-100 dark:hover:border-red-900/40 active:scale-95"
              title={t('patients.delete_patient')}
            >
              <Trash2 className="w-5 h-5" />
            </button>
          </>
        }
      />

      <div className="grid grid-cols-1 2xl:grid-cols-4 gap-8 items-start">
        {/* Main Content Area: Content & Summaries */}
        <div className="2xl:col-span-3 order-2 2xl:order-1 space-y-8 min-w-0">
          
          {/* Middle Section: Quick Clinical Overview (Now part of main flow) */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {patientId && <AllergySummary patientId={patientId} />}
            {patientId && <MedicationSummary patientId={patientId} />}
            {patientId && <BiomarkerSummary patientId={patientId} />}
          </div>

          {/* Activity Overview: rich summary cards (replaces former tabbed area) */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {patientId && <ExaminationSummary patientId={patientId} />}
            {patientId && <ClinicalEventSummary patientId={patientId} />}
            {patientId && <ScheduleSummary patientId={patientId} />}
          </div>
        </div>

        {/* Sidebar: Personal Info (Top on small, Side on wide) */}
        <div className="2xl:col-span-1 order-1 2xl:order-2">
          <div className="bg-gray-50 dark:bg-dark-bg/30 rounded-[2rem] p-6 2xl:p-8 border border-gray-100 dark:border-dark-border shadow-sm">
            <div className="flex items-center space-x-2 mb-6">
              <User className="w-4 h-4 text-gray-400" />
              <h4 className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('patients.personal_info')}</h4>
            </div>

            <div className="grid grid-cols-2 gap-4 2xl:block 2xl:space-y-6">
              <div>
                <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-2">{t('patients.mrn')}</p>
                <div className="flex items-baseline space-x-2">
                  <span className="text-[10px] font-mono font-black bg-white dark:bg-dark-surface px-3 py-1.5 rounded-lg border border-gray-200 dark:border-dark-border shadow-sm text-gray-700 dark:text-dark-text tracking-tight">{patient.mrn || '—'}</span>
                </div>
              </div>

              <div>
                <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-2">{t('patients.dob')}</p>
                <div className="flex flex-col">
                  <span className="text-sm font-black text-gray-700 dark:text-dark-text leading-none">
                    {patient.birth_date || '—'}
                  </span>
                  {patient.birth_date && (
                    <span className="text-[10px] font-bold text-blue-600 dark:text-blue-400 uppercase mt-1 tracking-wider">
                      {formatAge(patient.birth_date)}
                    </span>
                  )}
                </div>
              </div>

              <div className="pt-0 2xl:pt-4 border-t-0 2xl:border-t border-gray-100 dark:border-white/5 col-span-2 2xl:col-span-1">
                <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-1">{t('patients.gender')}</p>
                <p className="text-sm font-black text-gray-700 dark:text-dark-text capitalize leading-none">
                  {patient.gender || t('patients.unknown')}
                </p>
              </div>
            </div>
          </div>

          {/* Setup completion card — backend-derived checklist */}
          <div className="mt-4">
            <SetupChecklistCard patientId={patient.id} />
          </div>
        </div>
      </div>

      <PatientFormWizard
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        patient={patient}
        onSaved={(updated) => { setPatient(updated); setIsModalOpen(false); }}
      />
    </div>
  );
}

export default PatientDetail;
