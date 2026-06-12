import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { getDoctor, deleteDoctor, Doctor } from '../../services/doctorService';
import { getExaminations } from '../../services/examinationService';
import { useUIStore } from '../../store/slices/uiSlice';
import { 
  User, Stethoscope, Mail, Phone, ShieldCheck, 
  Trash2, Edit2, ChevronRight, FileText, Calendar,
  ArrowLeft, MapPin, Building
} from 'lucide-react';
import { LoadingState } from '../../components/ui/LoadingState';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';

function DoctorDetail() {
  const { t } = useTranslation();
  const { doctorId } = useParams<{ doctorId: string }>();
  const navigate = useNavigate();
  const showConfirmation = useUIStore(state => state.showConfirmation);
  
  const [doctor, setDoctor] = useState<Doctor | null>(null);
  const [examinations, setExaminations] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (doctorId) {
      loadData();
    }
  }, [doctorId]);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [doctorData, allExams] = await Promise.all([
        getDoctor(doctorId!),
        getExaminations() // Fetching all and filtering below for now
      ]);
      setDoctor(doctorData);
      
      // Filter examinations where this doctor is involved
      const doctorExams = allExams.filter((exam: any) => 
        exam.doctors?.some((d: any) => d.id === doctorId)
      );
      setExaminations(doctorExams);
    } catch (err) {
      console.error('Failed to load doctor details:', err);
      setError('Failed to load doctor details.');
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = () => {
    if (!doctor) return;
    showConfirmation({
      title: t('doctors.delete_doctor_title'),
      message: t('doctors.delete_doctor_confirm', { name: doctor.name }),
      confirmLabel: t('common.delete'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deleteDoctor(doctor.id);
          navigate('/doctors');
        } catch (err) {
          console.error('Failed to delete doctor:', err);
          alert(t('doctors.failed_delete'));
        }
      }
    });
  };

  if (loading) return <LoadingState showText message="Loading doctor details..." />;
  
  if (error || !doctor) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <Stethoscope className="w-16 h-16 text-gray-300 mb-4" />
        <h2 className="text-2xl font-bold text-gray-900 dark:text-dark-text">{error || 'Doctor not found'}</h2>
        <button 
          onClick={() => navigate('/doctors')}
          className="mt-6 flex items-center space-x-2 px-6 py-2 bg-blue-600 text-white rounded-xl font-bold"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>Back to Doctors</span>
        </button>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto space-y-8 pb-10">
      <PageHeader
        title={`${t('doctors.dr')} ${doctor.name}`}
        subtitle={doctor.specialty || t('doctors.general_practitioner')}
        icon={<User className="w-8 h-8" />}
        breadcrumbs={[
          { label: t('doctors.title'), path: '/doctors' }
        ]}
        showBackButton={true}
      />

      <StickyToolbar
        actions={
          <div className="flex items-center space-x-3">
            <button 
              onClick={() => navigate('/doctors', { state: { editingDoctor: doctor } })}
              className="flex items-center space-x-2 px-4 py-2 border border-gray-200 dark:border-dark-border text-gray-700 dark:text-dark-text rounded-xl hover:bg-gray-50 dark:hover:bg-dark-surface transition-all font-bold active:scale-95"
            >
              <Edit2 className="w-4 h-4" />
              <span>{t('common.edit')}</span>
            </button>
            <button 
              onClick={handleDelete}
              className="p-2.5 text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-xl transition-colors border border-transparent hover:border-red-100 dark:hover:border-red-900/40 active:scale-95"
            >
              <Trash2 className="w-5 h-5" />
            </button>
          </div>
        }
      />

      {/* Profile Info Card */}
      <div className="bg-white dark:bg-dark-surface rounded-3xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden">
        <div className="p-8 sm:p-10 flex flex-col md:flex-row items-center md:items-start gap-10">
          <div className="w-40 h-40 bg-blue-50 dark:bg-blue-900/30 rounded-[3rem] flex items-center justify-center text-blue-600 border border-blue-100 dark:border-blue-800 shadow-inner relative flex-shrink-0">
            <User className="w-20 h-20" />
            {doctor.license_number && (
               <div className="absolute -bottom-2 -right-2 bg-green-500 text-white p-2 rounded-2xl shadow-lg border-4 border-white dark:border-dark-surface" title="Verified License">
                  <ShieldCheck className="w-5 h-5" />
               </div>
            )}
          </div>
          <div className="flex-1 text-center md:text-left space-y-6">
            <div>
              <h1 className="text-4xl font-black text-[#1a2b4b] dark:text-dark-text tracking-tight mb-2">
                {t('doctors.dr')} {doctor.name}
              </h1>
              <p className="text-xl font-bold text-blue-600 dark:text-blue-400">
                {doctor.specialty || t('doctors.general_practitioner')}
              </p>
            </div>
            
            <div className="grid grid-cols-1 md:grid-cols-2 gap-8 pt-4">
              {/* Contact Information */}
              <div className="space-y-4">
                <h3 className="text-sm font-black uppercase tracking-widest text-gray-400 flex items-center space-x-2">
                  <Phone className="w-4 h-4" />
                  <span>Contact Information</span>
                </h3>
                <div className="space-y-3">
                  {doctor.phone && (
                    <div className="flex items-center space-x-3 text-gray-600 dark:text-dark-muted">
                      <div className="p-2 bg-blue-50 dark:bg-blue-900/20 rounded-lg">
                        <Phone className="w-4 h-4 text-blue-600" />
                      </div>
                      <div className="text-left">
                        <p className="text-[10px] font-black uppercase tracking-widest text-gray-400">Primary Phone</p>
                        <p className="font-bold">{doctor.phone}</p>
                      </div>
                    </div>
                  )}
                  {doctor.email && (
                    <div className="flex items-center space-x-3 text-gray-600 dark:text-dark-muted">
                      <div className="p-2 bg-blue-50 dark:bg-blue-900/20 rounded-lg">
                        <Mail className="w-4 h-4 text-blue-600" />
                      </div>
                      <div className="text-left">
                        <p className="text-[10px] font-black uppercase tracking-widest text-gray-400">Primary Email</p>
                        <p className="font-bold truncate max-w-[200px]">{doctor.email}</p>
                      </div>
                    </div>
                  )}
                  {doctor.telecom?.map((item, idx) => (
                    <div key={idx} className="flex items-center space-x-3 text-gray-600 dark:text-dark-muted">
                      <div className="p-2 bg-gray-50 dark:bg-dark-bg rounded-lg">
                        {item.system === 'phone' ? <Phone className="w-4 h-4 text-gray-400" /> : <Mail className="w-4 h-4 text-gray-400" />}
                      </div>
                      <div className="text-left">
                        <p className="text-[10px] font-black uppercase tracking-widest text-gray-400">{item.system} ({item.use || 'work'})</p>
                        <p className="font-bold">{item.value}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Office Location */}
              <div className="space-y-4">
                <h3 className="text-sm font-black uppercase tracking-widest text-gray-400 flex items-center space-x-2">
                  <Building className="w-4 h-4" />
                  <span>Office & Location</span>
                </h3>
                <div className="space-y-3">
                  {(doctor.office_number || doctor.office_details) && (
                    <div className="flex items-center space-x-3 text-gray-600 dark:text-dark-muted">
                      <div className="p-2 bg-indigo-50 dark:bg-indigo-900/20 rounded-lg">
                        <Building className="w-4 h-4 text-indigo-600" />
                      </div>
                      <div className="text-left">
                        <p className="text-[10px] font-black uppercase tracking-widest text-gray-400">Office</p>
                        <p className="font-bold">
                          {doctor.office_number && `Room ${doctor.office_number}`}
                          {doctor.office_number && doctor.office_details && ' - '}
                          {doctor.office_details}
                        </p>
                      </div>
                    </div>
                  )}
                  {doctor.address && (doctor.address.line || doctor.address.city) && (
                    <div className="flex items-center space-x-3 text-gray-600 dark:text-dark-muted">
                      <div className="p-2 bg-indigo-50 dark:bg-indigo-900/20 rounded-lg">
                        <MapPin className="w-4 h-4 text-indigo-600" />
                      </div>
                      <div className="text-left">
                        <p className="text-[10px] font-black uppercase tracking-widest text-gray-400">Location</p>
                        <p className="font-bold">
                          {doctor.address.line?.[0]}
                          {doctor.address.line?.[0] && (doctor.address.city || doctor.address.postalCode) && ', '}
                          {doctor.address.city} {doctor.address.postalCode}
                          {doctor.address.country && ` (${doctor.address.country})`}
                        </p>
                      </div>
                    </div>
                  )}
                  {doctor.license_number && (
                    <div className="flex items-center space-x-3 text-gray-600 dark:text-dark-muted">
                      <div className="p-2 bg-green-50 dark:bg-green-900/20 rounded-lg">
                        <ShieldCheck className="w-4 h-4 text-green-600" />
                      </div>
                      <div className="text-left">
                        <p className="text-[10px] font-black uppercase tracking-widest text-gray-400">License Number</p>
                        <p className="font-bold">{doctor.license_number}</p>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Activity Section */}
      <div className="grid grid-cols-1 gap-8">
        <div className="bg-white dark:bg-dark-surface rounded-3xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden">
          <div className="px-8 py-6 border-b border-gray-50 dark:border-dark-border flex items-center justify-between">
            <div className="flex items-center space-x-3">
              <div className="p-2 bg-blue-50 dark:bg-blue-900/20 rounded-xl">
                <FileText className="w-5 h-5 text-blue-600" />
              </div>
              <h2 className="text-xl font-bold text-[#1a2b4b] dark:text-dark-text tracking-tight">Clinical Involvement</h2>
            </div>
            <span className="px-4 py-1.5 bg-gray-100 dark:bg-dark-bg text-gray-500 dark:text-dark-muted text-xs font-black uppercase tracking-widest rounded-full">
              {examinations.length} Examinations
            </span>
          </div>

          <div className="p-0">
            {examinations.length === 0 ? (
              <div className="p-12 text-center">
                <div className="w-16 h-16 bg-gray-50 dark:bg-dark-bg rounded-full flex items-center justify-center mx-auto mb-4 border border-gray-100 dark:border-dark-border">
                  <FileText className="w-8 h-8 text-gray-300" />
                </div>
                <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">No examinations recorded</h3>
                <p className="text-gray-500 mt-1 max-w-xs mx-auto">This doctor hasn't been linked to any clinical visits yet.</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="bg-gray-50/50 dark:bg-dark-bg/30">
                      <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest">Date</th>
                      <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest">Category</th>
                      <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest">Notes</th>
                      <th className="px-8 py-4 text-right text-[10px] font-black text-gray-400 uppercase tracking-widest">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50 dark:divide-dark-border">
                    {examinations.map((exam) => (
                      <tr key={exam.id} className="hover:bg-gray-50/50 dark:hover:bg-dark-bg/50 transition-colors group">
                        <td className="px-8 py-5">
                          <div className="flex items-center space-x-3">
                            <Calendar className="w-4 h-4 text-gray-400" />
                            <span className="text-sm font-bold text-gray-900 dark:text-dark-text">
                              {new Date(exam.examination_date).toLocaleDateString()}
                            </span>
                          </div>
                        </td>
                        <td className="px-8 py-5">
                          <span className="px-3 py-1 bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 text-[10px] font-black uppercase tracking-widest rounded-full border border-blue-100 dark:border-blue-800/50">
                            {exam.category || 'General'}
                          </span>
                        </td>
                        <td className="px-8 py-5 max-w-md">
                          <p className="text-sm text-gray-500 dark:text-dark-muted truncate">
                            {exam.notes ? exam.notes.replace(/<[^>]*>?/gm, '') : 'No notes recorded'}
                          </p>
                        </td>
                        <td className="px-8 py-5 text-right">
                          <button 
                            onClick={() => navigate(`/examinations/${exam.id}`)}
                            className="inline-flex items-center text-xs font-black text-blue-600 dark:text-blue-400 uppercase tracking-widest hover:underline group"
                          >
                            View Visit
                            <ChevronRight className="w-3 h-3 ml-1 group-hover:translate-x-0.5 transition-transform" />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default DoctorDetail;
