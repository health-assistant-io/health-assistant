import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { 
  User as UserIcon, 
  Mail, 
  Shield, 
  Calendar, 
  Link as LinkIcon, 
  Stethoscope, 
  Building,
  ArrowLeft,
  Settings,
  Activity,
  ChevronRight,
  ShieldCheck,
  Building2,
  Database,
  Trash2
} from 'lucide-react';
import { getUser, User } from '../../services/userService';
import { listPatients, updatePatient } from '../../services/fhirService';
import { listDoctors, updateDoctor } from '../../services/doctorService';
import { getTenant, Tenant } from '../../services/tenantService';
import { useUIStore } from '../../store/slices/uiSlice';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { LoadingState } from '../../components/ui/LoadingState';

function UserDetail() {
  const { t } = useTranslation();
  const { userId } = useParams<{ userId: string }>();
  const navigate = useNavigate();
  const showConfirmation = useUIStore(state => state.showConfirmation);
  
  const [user, setUser] = useState<User | null>(null);
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [linkedPatients, setLinkedPatients] = useState<any[]>([]);
  const [linkedDoctors, setLinkedDoctors] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (userId) {
      loadData();
    }
  }, [userId]);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const userData = await getUser(userId!);
      setUser(userData);
      
      const promises: Promise<any>[] = [
        listPatients(undefined, 10, 0, userId),
        listDoctors(userId)
      ];

      if (userData.tenant_id) {
        promises.push(getTenant(userData.tenant_id));
      } else {
        promises.push(Promise.resolve(null));
      }

      const [patientsRes, doctorsData, tenantData] = await Promise.all(promises);
      
      setLinkedPatients(patientsRes.items || []);
      setLinkedDoctors(doctorsData || []);
      setTenant(tenantData);
    } catch (err) {
      console.error('Failed to load user details:', err);
      setError('Failed to load user details.');
    } finally {
      setLoading(false);
    }
  };

  const handleUnlink = async (type: 'patient' | 'doctor', recordId: string) => {
    showConfirmation({
      title: t('admin.unlink'),
      message: type === 'patient' ? t('admin.unlink_confirm_patient') : t('admin.unlink_confirm_doctor'),
      confirmLabel: t('admin.unlink'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          if (type === 'patient') {
            await updatePatient(recordId, { user_id: null } as any);
          } else {
            await updateDoctor(recordId, { user_id: null } as any);
          }
          await loadData();
        } catch (err) {
          console.error(`Failed to unlink ${type}:`, err);
        }
      }
    });
  };

  if (loading) return <LoadingState showText message="Loading user profile..." />;
  
  if (error || !user) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <UserIcon className="w-16 h-16 text-gray-300 mb-4" />
        <h2 className="text-2xl font-bold text-gray-900 dark:text-dark-text">{error || 'User not found'}</h2>
        <button 
          onClick={() => {
            const basePath = location.pathname.startsWith('/admin/system') ? '/admin/system/users' : '/admin/tenant/users';
            navigate(basePath);
          }}
          className="mt-6 flex items-center space-x-2 px-6 py-2 bg-blue-600 text-white rounded-xl font-bold"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>Back to People</span>
        </button>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto space-y-8 pb-10">
      <PageHeader
        title={user.email}
        subtitle={`Account ID: ${user.id.substring(0, 8)}...`}
        icon={<UserIcon className="w-8 h-8" />}
        breadcrumbs={[
          { label: t('admin.user_management'), path: location.pathname.startsWith('/admin/system') ? '/admin/system/users' : '/admin/tenant/users' }
        ]}
        showBackButton={true}
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Left Column: Account Info */}
        <div className="lg:col-span-1 space-y-6">
          <div className="bg-white dark:bg-dark-surface rounded-3xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden p-8">
            <div className="w-24 h-24 bg-gradient-to-br from-blue-500 to-indigo-600 rounded-3xl flex items-center justify-center text-white text-3xl font-black shadow-lg mx-auto mb-6">
              {user.email[0].toUpperCase()}
            </div>
            
            <div className="text-center space-y-1 mb-8">
              <h2 className="text-xl font-bold text-[#1a2b4b] dark:text-dark-text">{user.email}</h2>
              <span className="inline-block px-3 py-1 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 text-[10px] font-black uppercase tracking-widest rounded-full">
                {user.role}
              </span>
            </div>

            <div className="space-y-4 pt-6 border-t border-gray-50 dark:border-dark-border">
              <div className="flex items-center space-x-3">
                <div className="p-2 bg-gray-50 dark:bg-dark-bg rounded-lg shrink-0">
                  <Database className="w-4 h-4 text-gray-400" />
                </div>
                <div>
                  <p className="text-[9px] font-black uppercase tracking-widest text-gray-400">Installation</p>
                  <p className="font-bold text-sm text-gray-700 dark:text-dark-text">{tenant?.name || 'Unknown'}</p>
                </div>
              </div>

              <div className="flex items-center space-x-3">
                <div className="p-2 bg-gray-50 dark:bg-dark-bg rounded-lg shrink-0">
                  <ShieldCheck className="w-4 h-4 text-gray-400" />
                </div>
                <div>
                  <p className="text-[9px] font-black uppercase tracking-widest text-gray-400">Status</p>
                  <p className="font-bold text-sm text-green-600 uppercase">Active</p>
                </div>
              </div>
            </div>
          </div>

          {/* Quick Actions */}
          <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-6">
             <h3 className="font-bold text-gray-900 dark:text-dark-text mb-4">Quick Actions</h3>
             <div className="space-y-2">
                <button 
                  onClick={() => navigate('/settings')}
                  className="w-full flex items-center justify-between p-3 bg-gray-50 dark:bg-dark-bg hover:bg-gray-100 dark:hover:bg-dark-border rounded-xl transition-colors group"
                >
                  <div className="flex items-center space-x-3">
                    <Settings className="w-4 h-4 text-gray-400 group-hover:text-blue-500" />
                    <span className="text-sm font-semibold">Account Settings</span>
                  </div>
                  <ChevronRight className="w-4 h-4 text-gray-300" />
                </button>
             </div>
          </div>
        </div>

        {/* Right Column: Linked Records */}
        <div className="lg:col-span-2 space-y-8">
          {/* Linked Clinical Profiles */}
          <div className="bg-white dark:bg-dark-surface rounded-3xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden">
            <div className="px-8 py-6 border-b border-gray-100 dark:border-dark-border flex items-center justify-between bg-gray-50/30 dark:bg-dark-bg/20">
               <div className="flex items-center space-x-3">
                  <div className="p-2 bg-indigo-50 dark:bg-indigo-900/20 rounded-xl">
                    <LinkIcon className="w-5 h-5 text-indigo-600" />
                  </div>
                  <h3 className="text-lg font-bold text-[#1a2b4b] dark:text-dark-text tracking-tight">Linked Clinical Profiles</h3>
               </div>
            </div>

            <div className="p-8 space-y-8">
              {/* Linked Patients */}
              <div>
                <h4 className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-4 flex items-center">
                  <UserIcon className="w-3 h-3 mr-2" /> Linked Patients
                </h4>
                {linkedPatients.length === 0 ? (
                  <div className="p-6 bg-gray-50 dark:bg-dark-bg/50 rounded-2xl border border-dashed border-gray-200 dark:border-dark-border text-center">
                    <p className="text-sm text-gray-500 italic">No patient records linked to this account.</p>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {linkedPatients.map(patient => (
                      <div 
                        key={patient.id}
                        className="flex items-center justify-between p-4 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-2xl hover:border-blue-500 hover:shadow-md transition-all group"
                      >
                        <div className="flex items-center space-x-4 cursor-pointer" onClick={() => navigate(`/patients/${patient.id}`)}>
                          <div className="w-12 h-12 rounded-xl bg-blue-50 dark:bg-blue-900/30 flex items-center justify-center text-blue-600 font-bold">
                             {patient.name?.family?.[0] || 'P'}
                          </div>
                          <div>
                            <p className="font-bold text-[#1a2b4b] dark:text-dark-text">{patient.name?.given?.join(' ')} {patient.name?.family}</p>
                            <p className="text-[10px] text-gray-500 uppercase font-black tracking-widest">MRN: {patient.mrn || 'N/A'}</p>
                          </div>
                        </div>
                        <div className="flex items-center space-x-2">
                           <button 
                             onClick={() => handleUnlink('patient', patient.id)}
                             className="p-2 text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-xl transition-all opacity-0 group-hover:opacity-100"
                             title={t('admin.unlink')}
                           >
                             <Trash2 className="w-4 h-4" />
                           </button>
                           <ChevronRight className="w-5 h-5 text-gray-300 group-hover:text-blue-500 transition-colors" />
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Linked Doctors */}
              <div>
                <h4 className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-4 flex items-center">
                  <Stethoscope className="w-3 h-3 mr-2" /> Linked Professional Profiles
                </h4>
                {linkedDoctors.length === 0 ? (
                  <div className="p-6 bg-gray-50 dark:bg-dark-bg/50 rounded-2xl border border-dashed border-gray-200 dark:border-dark-border text-center">
                    <p className="text-sm text-gray-500 italic">No professional doctor profiles linked to this account.</p>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {linkedDoctors.map(doctor => (
                      <div 
                        key={doctor.id}
                        className="flex items-center justify-between p-4 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-2xl hover:border-blue-500 hover:shadow-md transition-all group"
                      >
                        <div className="flex items-center space-x-4 cursor-pointer" onClick={() => navigate(`/doctors/${doctor.id}`)}>
                          <div className="w-12 h-12 rounded-xl bg-emerald-50 dark:bg-emerald-900/30 flex items-center justify-center text-emerald-600">
                             <Stethoscope className="w-6 h-6" />
                          </div>
                          <div>
                            <p className="font-bold text-[#1a2b4b] dark:text-dark-text">Dr. {doctor.name}</p>
                            <p className="text-[10px] text-gray-500 uppercase font-black tracking-widest">{doctor.specialty || 'General Practitioner'}</p>
                          </div>
                        </div>
                        <div className="flex items-center space-x-2">
                           <button 
                             onClick={() => handleUnlink('doctor', doctor.id)}
                             className="p-2 text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-xl transition-all opacity-0 group-hover:opacity-100"
                             title={t('admin.unlink')}
                           >
                             <Trash2 className="w-4 h-4" />
                           </button>
                           <ChevronRight className="w-5 h-5 text-gray-300 group-hover:text-blue-500 transition-colors" />
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Activity / System Info Section */}
          <div className="bg-white dark:bg-dark-surface rounded-3xl shadow-sm border border-gray-100 dark:border-dark-border p-8">
             <div className="flex items-center space-x-3 mb-6">
                <Activity className="w-5 h-5 text-indigo-500" />
                <h3 className="font-bold text-gray-900 dark:text-dark-text">Account Metadata</h3>
             </div>
             <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                <div className="p-4 bg-gray-50/50 dark:bg-dark-bg/30 rounded-2xl">
                   <p className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1">User Identifier</p>
                   <p className="font-mono text-xs break-all">{user.id}</p>
                </div>
                <div className="p-4 bg-gray-50/50 dark:bg-dark-bg/30 rounded-2xl">
                   <p className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1">Tenant ID</p>
                   <p className="font-mono text-xs break-all">{user.tenant_id}</p>
                </div>
             </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default UserDetail;
