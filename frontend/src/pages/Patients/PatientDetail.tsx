import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { getPatient, updatePatient, deletePatient } from '../../services/patientService';
import { Patient } from '../../types/patient';
import { Edit2, Trash2, Fingerprint, X, Save, User } from 'lucide-react';
import { useUIStore } from '../../store/slices/uiSlice';
import { usePatientStore } from '../../store/slices/patientSlice';
import { AllergySummary } from '../../components/patients/AllergySummary';
import { MedicationSummary } from '../../components/patients/MedicationSummary';
import BiomarkerSummary from '../../components/patients/BiomarkerSummary';
import ExaminationSummary from '../../components/patients/ExaminationSummary';
import ClinicalEventSummary from '../../components/patients/ClinicalEventSummary';
import ScheduleSummary from '../../components/patients/ScheduleSummary';
import { formatAge } from '../../utils/dateUtils';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';

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
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [formData, setFormData] = useState({
    firstName: '',
    lastName: '',
    gender: 'unknown',
    birthDate: '',
    mrn: ''
  });

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
    if (!patient) return;
    setFormData({
      firstName: patient.name?.given?.[0] || '',
      lastName: patient.name?.family || '',
      gender: patient.gender || 'unknown',
      birthDate: patient.birth_date || '',
      mrn: patient.mrn || ''
    });
    setError(null);
    setIsModalOpen(true);
  };

  const handleEditSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);

    try {
      const payload: any = {
        name: {
          given: [formData.firstName],
          family: formData.lastName
        },
        gender: formData.gender,
        birth_date: formData.birthDate,
        mrn: formData.mrn
      };

      await updatePatient(patientId!, payload);
      await loadData();
      setIsModalOpen(false);
    } catch (err: any) {
      setError(err.response?.data?.detail || t('patients.failed_save'));
    } finally {
      setSubmitting(false);
    }
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
        icon={<User className="w-8 h-8" />}
        breadcrumbs={[
          { label: t('patients.directory'), path: '/patients' }
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
        </div>
      </div>

      {isModalOpen && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-[1000] p-4 animate-in fade-in duration-200">
          <div className="bg-white dark:bg-dark-surface rounded-2xl w-full max-w-md shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200">
            <div className="px-6 py-4 border-b border-gray-100 dark:border-dark-border flex justify-between items-center bg-white dark:bg-dark-surface sticky top-0 z-10">
              <h2 className="text-xl font-bold text-gray-900 dark:text-dark-text">
                {t('patients.update_profile_title')}
              </h2>
              <button onClick={() => setIsModalOpen(false)} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-border rounded-full transition-colors">
                <X className="w-5 h-5 text-gray-500" />
              </button>
            </div>
            
            <form onSubmit={handleEditSubmit} className="p-6 space-y-4">
              {error && (
                <div className="p-3 bg-red-50 border border-red-100 text-red-600 text-sm rounded-xl flex items-start space-x-2">
                  <Fingerprint className="w-4 h-4 mt-0.5 flex-shrink-0" />
                  <span>{error}</span>
                </div>
              )}

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1.5">{t('patients.first_name')} *</label>
                  <input
                    type="text"
                    required
                    className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all dark:text-dark-text"
                    value={formData.firstName}
                    onChange={(e) => setFormData({ ...formData, firstName: e.target.value })}
                  />
                </div>
                <div>
                  <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1.5">{t('patients.last_name')} *</label>
                  <input
                    type="text"
                    required
                    className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all dark:text-dark-text"
                    value={formData.lastName}
                    onChange={(e) => setFormData({ ...formData, lastName: e.target.value })}
                  />
                </div>
              </div>

              <div>
                <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1.5">{t('patients.dob')}</label>
                <input
                  type="date"
                  className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all dark:text-dark-text"
                  value={formData.birthDate}
                  onChange={(e) => setFormData({ ...formData, birthDate: e.target.value })}
                />
              </div>

              <div>
                <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1.5">{t('patients.gender')} *</label>
                <div className="grid grid-cols-2 gap-2">
                  {['male', 'female', 'other', 'unknown'].map((g) => (
                    <button
                      key={g}
                      type="button"
                      onClick={() => setFormData({ ...formData, gender: g as any })}
                      className={`px-4 py-2 rounded-xl text-sm font-medium border transition-all ${
                        formData.gender?.toLowerCase() === g 
                          ? 'bg-blue-600 border-blue-600 text-white shadow-md' 
                          : 'bg-white dark:bg-dark-bg border-gray-200 dark:border-dark-border text-gray-600 dark:text-dark-muted hover:bg-gray-50'
                      }`}
                    >
                      {g.charAt(0).toUpperCase() + g.slice(1)}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1.5">{t('patients.mrn')}</label>
                <input
                  type="text"
                  className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all dark:text-dark-text"
                  value={formData.mrn}
                  onChange={(e) => setFormData({ ...formData, mrn: e.target.value })}
                  placeholder="e.g. PAT-123456"
                />
              </div>

              <div className="pt-6 flex space-x-3">
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
                  className="flex-1 px-4 py-2.5 bg-emerald-600 text-white rounded-xl hover:bg-emerald-700 transition-all font-bold flex items-center justify-center space-x-2 shadow-lg shadow-emerald-200 dark:shadow-none disabled:opacity-50 active:scale-95"
                >
                  {submitting ? (
                    <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin"></div>
                  ) : (
                    <>
                      <Save className="w-4 h-4" />
                      <span>{t('common.save')}</span>
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

export default PatientDetail;
