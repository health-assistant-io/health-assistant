import { useEffect, useState, useRef } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { getPatient, updatePatient, deletePatient } from '../../services/fhirService';
import { getExaminations } from '../../services/examinationService';
import { Patient } from '../../types/fhir';
import { Edit2, Trash2, Activity, Fingerprint, X, Save, Plus, User, ChevronRight, Stethoscope } from 'lucide-react';
import { useUIStore } from '../../store/slices/uiSlice';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useTabScroll } from '../../hooks/useTabScroll';
import { AllergySummary } from '../../components/patients/AllergySummary';
import { MedicationSummary } from '../../components/patients/MedicationSummary';
import { EventDashboard } from '../../components/events/EventDashboard';
import { formatAge } from '../../utils/dateUtils';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { UniversalCalendar } from '../../components/ui/UniversalCalendar';

function PatientDetail() {
  const { t } = useTranslation();
  const { patientId, activeTab: urlTab } = useParams<{ patientId: string, activeTab?: string }>();
  const navigate = useNavigate();
  const showConfirmation = useUIStore(state => state.showConfirmation);
  const { currentPatient, setCurrentPatient } = usePatientStore();
  const [patient, setPatient] = useState<Patient | null>(null);
  const [examinations, setExaminations] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'history' | 'events' | 'calendar'>((urlTab as any) || 'history');
  const tabsRef = useRef<HTMLDivElement>(null);

  // Auto-scroll when tab changes
  useTabScroll(tabsRef, activeTab);

  // Sync global patient when this patient is loaded
  useEffect(() => {
    if (patient && (!currentPatient || currentPatient.id !== patient.id)) {
      setCurrentPatient(patient);
    }
  }, [patient, currentPatient, setCurrentPatient]);
  
  // Sync tab with URL
  useEffect(() => {
    if (urlTab && (urlTab === 'history' || urlTab === 'events' || urlTab === 'calendar')) {
      setActiveTab(urlTab as any);
    }
  }, [urlTab]);

  const handleTabChange = (tab: 'history' | 'events' | 'calendar') => {
    setActiveTab(tab);
    navigate(`/patients/${patientId}/${tab}`, { replace: true });
  };
  
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
      const [patientData, examsData] = await Promise.all([
        getPatient(patientId!),
        getExaminations(patientId!)
      ]);
      setPatient(patientData);
      setExaminations(examsData || []);
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

  const stripHtml = (html: string) => {
    if (!html) return '—';
    const tmp = document.createElement("DIV");
    tmp.innerHTML = html;
    return tmp.textContent || tmp.innerText || '—';
  };

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
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {patientId && <AllergySummary patientId={patientId} />}
            {patientId && <MedicationSummary patientId={patientId} />}
          </div>

          {/* Main Content: Tabs & Tab Content */}
          <div ref={tabsRef} className="w-full flex flex-col min-h-0 scroll-mt-20">
            <div className="flex items-center space-x-1 bg-gray-100/50 dark:bg-dark-bg/50 p-1 rounded-2xl w-fit mb-6 border border-gray-100 dark:border-dark-border overflow-x-auto no-scrollbar">
              <button
                onClick={() => handleTabChange('history')}
                className={`px-6 py-2.5 rounded-xl text-xs font-bold uppercase tracking-widest transition-all whitespace-nowrap ${
                  activeTab === 'history' 
                    ? 'bg-white dark:bg-dark-surface shadow-sm text-blue-600' 
                    : 'text-gray-400 hover:text-gray-600'
                }`}
              >
                {t('patients.examination_history')}
              </button>
              <button
                onClick={() => handleTabChange('events')}
                className={`px-6 py-2.5 rounded-xl text-xs font-bold uppercase tracking-widest transition-all whitespace-nowrap ${
                  activeTab === 'events' 
                    ? 'bg-white dark:bg-dark-surface shadow-sm text-blue-600' 
                    : 'text-gray-400 hover:text-gray-600'
                }`}
              >
                {t('events.title')}
              </button>
              <button
                onClick={() => handleTabChange('calendar')}
                className={`px-6 py-2.5 rounded-xl text-xs font-bold uppercase tracking-widest transition-all whitespace-nowrap ${
                  activeTab === 'calendar' 
                    ? 'bg-white dark:bg-dark-surface shadow-sm text-blue-600' 
                    : 'text-gray-400 hover:text-gray-600'
                }`}
              >
                {t('common.calendar')}
              </button>
            </div>

            {activeTab === 'history' && (
              <div className="bg-white dark:bg-dark-surface rounded-[2rem] shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden">
                <div className="px-6 sm:px-8 py-6 border-b border-gray-50 dark:border-dark-border flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
                  <h2 className="text-lg font-bold text-gray-900 dark:text-dark-text tracking-tight">
                    {t('patients.examination_history')}
                  </h2>
                  <button 
                    onClick={() => navigate('/examinations/upload')}
                    className="flex items-center space-x-1.5 px-3 py-1.5 bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 rounded-lg hover:bg-blue-100 transition-all text-[10px] font-black uppercase tracking-widest shadow-sm border border-blue-100/50 dark:border-blue-800/30 active:scale-95 w-full sm:w-auto justify-center"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    <span>{t('patients.add_visit')}</span>
                  </button>
                </div>
                
                {examinations.length === 0 ? (
                  <div className="p-8 text-center text-gray-400 italic">
                    {t('patients.no_exams_recorded')}
                  </div>
                ) : (
                  <div className="max-h-[600px] overflow-y-auto custom-scrollbar">
                    <table className="min-w-full divide-y divide-gray-100 dark:divide-dark-border">
                      <thead className="bg-gray-50/50 dark:bg-dark-bg/50 sticky top-0 z-10">
                        <tr>
                          <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">
                            {t('dashboard.config.date_range')}
                          </th>
                          <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">
                            {t('examinations.categories')}
                          </th>
                          <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">
                            {t('examinations.subtitle')}
                          </th>
                          <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">
                            {t('patients.clinician')}
                          </th>
                          <th className="px-8 py-4 text-right text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">
                            {t('documents_explorer.status')}
                          </th>
                        </tr>
                      </thead>
                      <tbody className="bg-white dark:bg-dark-surface divide-y divide-gray-50 dark:divide-dark-border">
                        {examinations.sort((a, b) => new Date(b.examination_date).getTime() - new Date(a.examination_date).getTime()).map((exam) => (
                          <tr key={exam.id} className="hover:bg-blue-50/30 dark:hover:bg-blue-900/10 transition-colors">
                            <td className="px-8 py-5 whitespace-nowrap text-sm font-bold">
                              <Link 
                                to={`/examinations/${exam.id}`} 
                                className="text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300 hover:underline flex items-center gap-1 group/link"
                              >
                                {new Date(exam.examination_date).toLocaleDateString()}
                                <ChevronRight className="w-3 h-3 opacity-0 group-hover/link:opacity-100 transition-opacity" />
                              </Link>
                            </td>
                            <td className="px-8 py-5 whitespace-nowrap">
                              <span className="px-3 py-1 bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 text-[10px] font-black uppercase tracking-widest rounded-full border border-blue-100 dark:border-blue-800/50">
                                {exam.category || 'General'}
                              </span>
                            </td>
                            <td className="px-8 py-5 text-sm text-gray-500 dark:text-dark-muted truncate max-w-[200px] font-medium">
                              {stripHtml(exam.notes)}
                            </td>
                            <td className="px-8 py-5 whitespace-nowrap">
                              {exam.doctors && exam.doctors.length > 0 ? (
                                <div className="flex items-center text-sm text-gray-700 dark:text-dark-text font-bold">
                                  <Stethoscope className="w-3.5 h-3.5 mr-1.5 text-gray-400" />
                                  {exam.doctors.map((d: any) => d.name).join(', ')}
                                </div>
                              ) : (
                                <span className="text-xs text-gray-400 italic font-medium">{t('patients.no_clinician')}</span>
                              )}
                            </td>
                            <td className="px-8 py-5 whitespace-nowrap text-right">
                              <span className={`inline-flex items-center px-2.5 py-1 rounded-lg text-[10px] font-black uppercase tracking-wider border ${
                                exam.extraction_status === 'completed' 
                                  ? 'bg-green-50 text-green-700 border-green-100' 
                                  : exam.extraction_status === 'failed'
                                  ? 'bg-red-50 text-red-700 border-red-100'
                                  : exam.extraction_status === 'processing'
                                  ? 'bg-yellow-50 text-yellow-700 border-yellow-100 animate-pulse'
                                  : 'bg-gray-50 text-gray-600 border-gray-100'
                              }`}>
                                {exam.extraction_status ? exam.extraction_status : t('patients.pending')}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}

            {activeTab === 'events' && (
              <div className="w-full">
                {patientId && <EventDashboard patientId={patientId} />}
              </div>
            )}

            {activeTab === 'calendar' && (
              <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
                <UniversalCalendar 
                  config={{ 
                    patientId: patientId,
                    types: ['medication', 'examination', 'allergy', 'clinical-event'] 
                  }} 
                  defaultView="timeline"
                />
              </div>
            )}
          </div>
        </div>

        {/* Sidebar: Personal Info & Activity (Top on small, Side on wide) */}
        <div className="2xl:col-span-1 order-1 2xl:order-2 grid grid-cols-1 md:grid-cols-2 2xl:grid-cols-1 gap-6">
          {/* Personal Info Box */}
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

          {/* Activity Box */}
          <div className="bg-white dark:bg-dark-surface rounded-[2.5rem] p-6 2xl:p-8 border border-gray-100 dark:border-dark-border shadow-sm h-full">
            <div className="flex items-center space-x-2 mb-6">
              <Activity className="w-4 h-4 text-gray-400" />
              <h4 className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('patients.patient_activity')}</h4>
            </div>
            <div className="space-y-6">
              <div className="flex justify-between items-center">
                <span className="text-xs text-gray-500 font-bold uppercase tracking-tighter">{t('patients.total_examinations')}</span>
                <span className="text-lg font-black text-gray-700 dark:text-dark-text leading-none">{examinations.length}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-xs text-gray-500 font-bold uppercase tracking-tighter">{t('patients.last_visit')}</span>
                <span className="text-xs font-black text-gray-700 dark:text-dark-text">
                  {examinations.length > 0 
                    ? new Date(Math.max(...examinations.map(e => new Date(e.examination_date).getTime()))).toLocaleDateString()
                    : '—'}
                </span>
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
