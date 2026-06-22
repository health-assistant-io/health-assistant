import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useAuthStore } from '../../store/slices/authSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import { listPatients, createPatient, updatePatient, deletePatient } from '../../services/patientService';
import { useNavigate } from 'react-router-dom';
import { Search, Plus, User, Edit2, Trash2, Calendar, Fingerprint, ChevronRight, Users, X, Save } from 'lucide-react';
import { calculateAge, formatAge } from '../../utils/dateUtils';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { useCreateIntent } from '../../hooks/useCreateIntent';

function Patients() {
  const { t } = useTranslation();
  const { patients, setPatients } = usePatientStore();
  const { user } = useAuthStore();
  const showConfirmation = useUIStore(state => state.showConfirmation);
  const navigate = useNavigate();
  
  const [loading, setLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingPatient, setEditingPatient] = useState<any>(null);
  const searchTerm = useUIStore(state => state.pageSearchTerm);
  const setSearchTerm = useUIStore(state => state.setPageSearchTerm);
  const setIsPageSearchSupported = useUIStore(state => state.setIsPageSearchSupported);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  // Form state
  const [formData, setFormData] = useState({
    firstName: '',
    lastName: '',
    gender: 'unknown',
    birthDate: '',
    mrn: ''
  });

  const fetchPatients = async () => {
    setLoading(true);
    try {
      // Omitting tenant_id ensures the backend uses the tenant_id from the
      // active JWT, correctly handling switched SYSTEM_ADMIN sessions.
      const response = await listPatients(undefined, 200);
      setPatients(response.items || []);
    } catch (error) {
      console.error('Failed to load patients:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPatients();
  }, []);

  useEffect(() => {
    setIsPageSearchSupported(true);
    return () => {
      setIsPageSearchSupported(false);
      setSearchTerm('');
    };
  }, [setIsPageSearchSupported, setSearchTerm]);

  const handleOpenModal = (patient: any = null) => {
    if (patient) {
      setEditingPatient(patient);
      setFormData({
        firstName: patient.name?.given?.[0] || '',
        lastName: patient.name?.family || '',
        gender: patient.gender || 'unknown',
        birthDate: patient.birth_date || '',
        mrn: patient.mrn || ''      });
    } else {
      setEditingPatient(null);
      setFormData({
        firstName: '',
        lastName: '',
        gender: 'unknown',
        birthDate: '',
        mrn: ''
      });
    }
    setError(null);
    setIsModalOpen(true);
  };

  // Open the create modal automatically when arrived via ?new=patient
  useCreateIntent(() => handleOpenModal(), 'patient');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);

    let currentTenantId = user?.tenant_id;
    if (!currentTenantId) {
      const token = localStorage.getItem('accessToken');
      if (token) {
        const decoded = JSON.parse(atob(token.split('.')[1]));
        currentTenantId = decoded.tenant_id;
      }
    }

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

      if (editingPatient) {
        await updatePatient(editingPatient.id, payload);
      } else {
        await createPatient(payload, currentTenantId!);
      }
      
      await fetchPatients();
      setIsModalOpen(false);
    } catch (err: any) {
      setError(err.response?.data?.detail || t('patients.failed_save'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = (id: string, name: string) => {
    showConfirmation({
      title: t('patients.delete_patient'),
      message: t('patients.delete_confirm', { name }),
      confirmLabel: t('common.delete'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deletePatient(id);
          await fetchPatients();
        } catch (err) {
          console.error('Failed to delete patient:', err);
          alert(t('patients.failed_delete'));
        }
      }
    });
  };

  const filteredPatients = patients.filter(p => {
    const fullName = `${p.name?.given?.join(' ')} ${p.name?.family}`.toLowerCase();
    return fullName.includes(searchTerm.toLowerCase()) || p.mrn?.toLowerCase().includes(searchTerm.toLowerCase());
  });

  const getGenderColor = (gender: string) => {
    switch(gender?.toLowerCase()) {
      case 'male': return 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400';
      case 'female': return 'bg-pink-100 dark:bg-pink-900/30 text-pink-700 dark:text-pink-400';
      default: return 'bg-gray-100 dark:bg-dark-bg text-gray-700 dark:text-dark-muted';
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('patients.directory')}
        subtitle={t('patients.manage_profiles')}
        icon={<Users className="w-8 h-8" />}
        breadcrumbs={[]}
      />

      <StickyToolbar
        actions={
          <>
            <button 
              onClick={() => handleOpenModal()}
              className="flex items-center space-x-2 px-6 py-2.5 bg-blue-600 text-white rounded-xl hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none font-bold active:scale-95"
            >
              <Plus className="w-4 h-4" />
              <span>{t('patients.add_patient')}</span>
            </button>
          </>
        }
      />

      {loading ? (
        <div className="flex flex-col items-center justify-center py-20">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mb-4"></div>
          <p className="text-gray-500 animate-pulse">{t('patients.loading_data')}</p>
        </div>
      ) : filteredPatients.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {filteredPatients.map((patient) => (
            <div 
              key={patient.id} 
              className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden hover:shadow-md transition-all group"
            >
              <div className="p-6">
                <div className="flex justify-between items-start mb-4">
                  <div className="w-14 h-14 bg-gray-50 dark:bg-dark-bg rounded-xl flex items-center justify-center border border-gray-100 dark:border-dark-border">
                    <User className="w-7 h-7 text-gray-400" />
                  </div>
                  <div className="flex items-center space-x-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button 
                      onClick={() => handleOpenModal(patient)}
                      className="p-2 text-gray-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-xl transition-all active:scale-95"
                      title={t('patients.edit_patient')}
                    >
                      <Edit2 className="w-4 h-4" />
                    </button>
                    <button 
                      onClick={() => handleDelete(patient.id, `${patient.name?.given?.[0]} ${patient.name?.family}`)}
                      className="p-2 text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-xl transition-all active:scale-95"
                      title={t('patients.delete_patient')}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                <div className="cursor-pointer" onClick={() => navigate(`/patients/${patient.id}`)}>
                  <h3 className="text-xl font-bold text-[#1a2b4b] dark:text-dark-text group-hover:text-blue-600 transition-colors">
                    {patient.name?.given?.join(' ')} {patient.name?.family}
                  </h3>
                  
                  <div className="flex items-center space-x-2 mt-2">
                    <span className={`px-2 py-0.5 text-[10px] font-bold uppercase rounded ${getGenderColor(patient.gender)}`}>
                      {patient.gender}
                    </span>
                    {patient.mrn && (
                      <span className="px-2 py-0.5 text-[10px] font-bold uppercase bg-gray-100 dark:bg-dark-bg text-gray-600 dark:text-dark-muted rounded">
                        {t('patients.mrn')}: {patient.mrn}
                      </span>
                    )}
                  </div>

                  <div className="mt-6 space-y-3">
                    <div className="flex items-center text-sm text-gray-500 dark:text-dark-muted">
                      <Calendar className="w-4 h-4 mr-2 text-gray-400" />
                      <span>
                        {t('patients.born')}: {patient.birth_date || 'Unknown'} 
                        { patient.birth_date && (
                          <span className="ml-1 text-gray-400 font-medium">
                            ({formatAge(patient.birth_date)})
                          </span>
                        )}
                      </span>
                    </div>
                    <div className="flex items-center text-sm text-gray-500 dark:text-dark-muted">
                      <Fingerprint className="w-4 h-4 mr-2 text-gray-400" />
                      <span className="truncate">ID: {patient.id.substring(0, 8)}...</span>
                    </div>
                  </div>
                </div>
              </div>

              <div className="px-6 py-4 bg-gray-50/50 dark:bg-dark-border/20 border-t border-gray-50 dark:border-dark-border flex items-center justify-between">
                <button 
                  onClick={() => navigate(`/patients/${patient.id}`)}
                  className="text-xs font-bold text-blue-600 dark:text-blue-400 uppercase tracking-wider flex items-center hover:underline"
                >
                  {t('patients.view_profile')}
                  <ChevronRight className="w-3 h-3 ml-1" />
                </button>
                <div className="flex -space-x-2">
                  <div className="w-6 h-6 rounded-full bg-blue-500 border-2 border-white dark:border-dark-surface flex items-center justify-center text-[10px] text-white font-bold">DR</div>
                  <div className="w-6 h-6 rounded-full bg-green-500 border-2 border-white dark:border-dark-surface flex items-center justify-center text-[10px] text-white font-bold">NS</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center py-20 bg-gray-50 dark:bg-dark-bg/30 rounded-3xl border-2 border-dashed border-gray-200 dark:border-dark-border">
          <div className="w-16 h-16 bg-white dark:bg-dark-surface rounded-full flex items-center justify-center shadow-sm mb-4">
            <Users className="w-8 h-8 text-gray-400" />
          </div>
          <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">{t('patients.no_patients')}</h3>
          <p className="text-gray-500 mt-1 mb-6 text-center max-w-xs">
            {searchTerm ? t('patients.no_results', { term: searchTerm }) : t('patients.start_adding')}
          </p>
          <button 
            onClick={() => searchTerm ? setSearchTerm('') : handleOpenModal()} 
            className="px-8 py-2.5 bg-blue-600 text-white rounded-xl font-bold hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none active:scale-95"
          >
            {searchTerm ? t('common.reset') : t('patients.add_first')}
          </button>
        </div>
      )}

      {/* Patient Modal */}
      {isModalOpen && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-[1000] p-4 animate-in fade-in duration-200">
          <div className="bg-white dark:bg-dark-surface rounded-2xl w-full max-w-md shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200">
            <div className="px-6 py-4 border-b border-gray-100 dark:border-dark-border flex justify-between items-center bg-white dark:bg-dark-surface sticky top-0 z-10">
              <h2 className="text-xl font-bold text-gray-900 dark:text-dark-text">
                {editingPatient ? t('patients.edit_profile') : t('patients.add_new')}
              </h2>
              <button onClick={() => setIsModalOpen(false)} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-border rounded-full transition-colors">
                <X className="w-5 h-5 text-gray-500 dark:text-dark-muted" />
              </button>
            </div>
            
            <form onSubmit={handleSubmit} className="p-6 space-y-4">
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
                    placeholder="John"
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
                    placeholder="Doe"
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
                  className="flex-1 px-4 py-2.5 bg-blue-600 text-white rounded-xl hover:bg-blue-700 transition-all font-bold flex items-center justify-center space-x-2 shadow-lg shadow-blue-200 dark:shadow-none disabled:opacity-50 active:scale-95"
                >
                  {submitting ? (
                    <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin"></div>
                  ) : (
                    <>
                      <Save className="w-4 h-4" />
                      <span>{editingPatient ? t('patients.update_profile') : t('patients.create_patient')}</span>
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

export default Patients;
