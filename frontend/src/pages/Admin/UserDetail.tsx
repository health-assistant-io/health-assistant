import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import {
  User as UserIcon,
  Shield,
  Link as LinkIcon,
  Stethoscope,
  ArrowLeft,
  ChevronRight,
  ShieldCheck,
  Database,
  Trash2,
  X,
  Save,
  CheckCircle2,
  UserPlus
} from 'lucide-react';
import { getUser, updateUser, User, UserRole } from '../../services/userService';
import { listPatients, updatePatient } from '../../services/patientService';
import { listDoctors, updateDoctor } from '../../services/doctorService';
import { getTenant, Tenant } from '../../services/tenantService';
import { useUIStore } from '../../store/slices/uiSlice';
import { PageHeader } from '../../components/ui/PageHeader';
import { LoadingState } from '../../components/ui/LoadingState';

const ROLE_OPTIONS: { value: UserRole; label: string; color: string }[] = [
  { value: 'SYSTEM_ADMIN', label: 'admin.role_system_admin', color: 'text-purple-600 bg-purple-50 dark:bg-purple-900/20' },
  { value: 'ADMIN', label: 'admin.role_admin', color: 'text-red-600 bg-red-50 dark:bg-red-900/20' },
  { value: 'MANAGER', label: 'admin.role_manager', color: 'text-blue-600 bg-blue-50 dark:bg-blue-900/20' },
  { value: 'USER', label: 'admin.role_user', color: 'text-gray-600 bg-gray-50 dark:bg-gray-700/20' }
];

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

  // Access-level editing
  const [targetRole, setTargetRole] = useState<UserRole>('USER');
  const [isEditingRole, setIsEditingRole] = useState(false);
  const [savingRole, setSavingRole] = useState(false);

  // Linking modal
  const [isLinkModalOpen, setIsLinkModalOpen] = useState(false);
  const [linkType, setLinkType] = useState<'patient' | 'doctor'>('patient');
  const [linkablePatients, setLinkablePatients] = useState<any[]>([]);
  const [linkableDoctors, setLinkableDoctors] = useState<any[]>([]);
  const [linkLoading, setLinkLoading] = useState(false);

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
      setTargetRole(userData.role);

      const promises: Promise<any>[] = [
        listPatients(undefined, 100, 0, userId),
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

  const handleSaveRole = async () => {
    if (!user) return;
    setSavingRole(true);
    try {
      const updated = await updateUser(user.id, { role: targetRole });
      setUser(updated);
      setIsEditingRole(false);
      toast.success(t('admin.role_updated'));
    } catch (err) {
      console.error('Failed to update access level:', err);
      toast.error(t('admin.role_update_failed'));
    } finally {
      setSavingRole(false);
    }
  };

  const handleCancelEditRole = () => {
    if (!user) return;
    setTargetRole(user.role);
    setIsEditingRole(false);
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

  const openLinkModal = async () => {
    setIsLinkModalOpen(true);
    setLinkLoading(true);
    try {
      const [allPatientsRes, allDoctors] = await Promise.all([
        listPatients(undefined, 100, 0),
        listDoctors()
      ]);
      // Exclude records already linked to this user
      const linkedPatientIds = new Set(linkedPatients.map(p => p.id));
      const linkedDoctorIds = new Set(linkedDoctors.map(d => d.id));
      setLinkablePatients((allPatientsRes.items || []).filter((p: any) => !linkedPatientIds.has(p.id)));
      setLinkableDoctors((allDoctors || []).filter((d: any) => !linkedDoctorIds.has(d.id)));
    } catch (err) {
      console.error('Failed to load linkable records:', err);
    } finally {
      setLinkLoading(false);
    }
  };

  const handleLinkRecord = async (recordId: string) => {
    if (!user) return;

    try {
      if (linkType === 'patient') {
        await updatePatient(recordId, { user_id: user.id } as any);
      } else {
        await updateDoctor(recordId, { user_id: user.id } as any);
      }
      toast.success(t('admin.link_success'));
      setIsLinkModalOpen(false);
      await loadData();
    } catch (err) {
      console.error('Failed to link record:', err);
      toast.error(t('admin.link_failed'));
    }
  };

  if (loading) return <LoadingState showText message="Loading user profile..." />;

  const basePath = location.pathname.startsWith('/admin/system') ? '/admin/system/users' : '/admin/tenant/users';

  if (error || !user) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <UserIcon className="w-16 h-16 text-gray-300 mb-4" />
        <h2 className="text-2xl font-bold text-gray-900 dark:text-dark-text">{error || 'User not found'}</h2>
        <button
          onClick={() => navigate(basePath)}
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
          { label: t('admin.user_management'), path: basePath }
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
              <h2 className="text-xl font-bold text-brand-navy dark:text-dark-text">{user.email}</h2>
              <span className={`inline-block px-3 py-1 text-[10px] font-black uppercase tracking-widest rounded-full ${
                ROLE_OPTIONS.find(r => r.value === user.role)?.color || 'bg-gray-100 text-gray-600'
              }`}>
                {t(ROLE_OPTIONS.find(r => r.value === user.role)?.label || 'admin.role_user')}
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

          {/* Access Level */}
          <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-6">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center space-x-3">
                <Shield className="w-5 h-5 text-blue-500" />
                <h3 className="font-bold text-gray-900 dark:text-dark-text">{t('admin.access_level')}</h3>
              </div>
              {!isEditingRole && (
                <button
                  onClick={() => setIsEditingRole(true)}
                  className="text-xs font-bold text-blue-600 hover:text-blue-700 uppercase tracking-widest"
                >
                  {t('common.edit')}
                </button>
              )}
            </div>

            {!isEditingRole ? (
              <div className={`inline-flex items-center px-3 py-1.5 rounded-full text-xs font-black uppercase tracking-wide ${
                ROLE_OPTIONS.find(r => r.value === user.role)?.color || 'bg-gray-100 text-gray-600'
              }`}>
                {t(ROLE_OPTIONS.find(r => r.value === user.role)?.label || 'admin.role_user')}
              </div>
            ) : (
              <div className="space-y-3">
                <div className="grid grid-cols-1 gap-2">
                  {ROLE_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => setTargetRole(option.value)}
                      className={`flex items-center justify-between p-3 rounded-2xl border-2 transition-all text-left ${
                        targetRole === option.value
                          ? 'border-blue-500 bg-blue-50/50 dark:bg-blue-900/20'
                          : 'border-gray-100 dark:border-dark-border hover:border-gray-200 dark:hover:border-gray-600'
                      }`}
                    >
                      <div className="flex items-center space-x-3">
                        <div className={`p-1.5 rounded-lg ${option.color}`}>
                          <Shield className="w-4 h-4" />
                        </div>
                        <div>
                          <p className="font-bold text-sm text-brand-navy dark:text-dark-text">{t(option.label)}</p>
                          <p className="text-[9px] text-gray-500 uppercase tracking-widest font-black">Scope: {option.value.includes('SYSTEM') ? 'Global' : 'Tenant'}</p>
                        </div>
                      </div>
                      {targetRole === option.value && (
                        <div className="w-5 h-5 rounded-full bg-blue-500 flex items-center justify-center text-white">
                          <CheckCircle2 className="w-3.5 h-3.5" />
                        </div>
                      )}
                    </button>
                  ))}
                </div>

                <div className="flex space-x-2 pt-2">
                  <button
                    type="button"
                    onClick={handleCancelEditRole}
                    disabled={savingRole}
                    className="flex-1 px-3 py-2 border border-gray-200 dark:border-dark-border rounded-xl hover:bg-gray-50 dark:hover:bg-dark-border transition-colors font-bold text-gray-600 dark:text-dark-muted text-sm"
                  >
                    {t('common.cancel')}
                  </button>
                  <button
                    type="button"
                    onClick={handleSaveRole}
                    disabled={savingRole}
                    className="flex-1 px-3 py-2 bg-brand-cyan text-white rounded-xl hover:bg-brand-cyan-hover transition-all font-bold flex items-center justify-center space-x-2 text-sm"
                  >
                    <Save className="w-4 h-4" />
                    <span>{t('common.save')}</span>
                  </button>
                </div>
              </div>
            )}
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
                  <h3 className="text-lg font-bold text-brand-navy dark:text-dark-text tracking-tight">Linked Clinical Profiles</h3>
               </div>
               <button
                 onClick={openLinkModal}
                 className="flex items-center space-x-2 px-4 py-2 bg-blue-600 text-white rounded-xl hover:bg-blue-700 transition-all text-sm font-bold"
               >
                 <UserPlus className="w-4 h-4" />
                 <span>{t('admin.link_record')}</span>
               </button>
            </div>

            <div className="p-8 space-y-8">
              {/* Linked Patients */}
              <div>
                <h4 className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-4 flex items-center">
                  <UserIcon className="w-3 h-3 mr-2" /> {t('common.patients')}
                </h4>
                {linkedPatients.length === 0 ? (
                  <div className="p-6 bg-gray-50 dark:bg-dark-bg/50 rounded-2xl border border-dashed border-gray-200 dark:border-dark-border text-center">
                    <p className="text-sm text-gray-500 italic">{t('admin.linked_patients_empty')}</p>
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
                            <p className="font-bold text-brand-navy dark:text-dark-text">{patient.name?.given?.join(' ')} {patient.name?.family}</p>
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
                  <Stethoscope className="w-3 h-3 mr-2" /> {t('common.doctors')}
                </h4>
                {linkedDoctors.length === 0 ? (
                  <div className="p-6 bg-gray-50 dark:bg-dark-bg/50 rounded-2xl border border-dashed border-gray-200 dark:border-dark-border text-center">
                    <p className="text-sm text-gray-500 italic">{t('admin.linked_doctors_empty')}</p>
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
                            <p className="font-bold text-brand-navy dark:text-dark-text">Dr. {doctor.name}</p>
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

          {/* Account Metadata */}
          <div className="bg-white dark:bg-dark-surface rounded-3xl shadow-sm border border-gray-100 dark:border-dark-border p-8">
             <div className="flex items-center space-x-3 mb-6">
                <Database className="w-5 h-5 text-indigo-500" />
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

      {/* Linking Modal */}
      {isLinkModalOpen && user && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-[1000] p-4 animate-in fade-in duration-200">
          <div className="bg-white dark:bg-dark-surface rounded-3xl w-full max-w-2xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200 flex flex-col max-h-[85vh]">
            <div className="px-8 py-6 border-b border-gray-100 dark:border-dark-border flex justify-between items-center bg-gray-50/50 dark:bg-dark-bg/50 shrink-0">
              <div>
                <h2 className="text-xl font-bold text-brand-navy dark:text-dark-text">{t('admin.link_new_record')}</h2>
                <p className="text-sm text-gray-500 dark:text-dark-muted">{t('admin.link_new_record_help', { email: user.email })}</p>
              </div>
              <button onClick={() => setIsLinkModalOpen(false)} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-border rounded-full transition-colors text-gray-400">
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="flex border-b border-gray-100 dark:border-dark-border shrink-0">
              <button
                onClick={() => setLinkType('patient')}
                className={`flex-1 py-4 text-sm font-bold border-b-2 transition-all ${linkType === 'patient' ? 'border-blue-500 text-blue-600 bg-blue-50/30 dark:bg-blue-900/10' : 'border-transparent text-gray-400 hover:text-gray-600'}`}
              >
                <div className="flex items-center justify-center space-x-2">
                  <UserIcon className="w-4 h-4" />
                  <span>{t('common.patients')}</span>
                </div>
              </button>
              <button
                onClick={() => setLinkType('doctor')}
                className={`flex-1 py-4 text-sm font-bold border-b-2 transition-all ${linkType === 'doctor' ? 'border-blue-500 text-blue-600 bg-blue-50/30 dark:bg-blue-900/10' : 'border-transparent text-gray-400 hover:text-gray-600'}`}
              >
                <div className="flex items-center justify-center space-x-2">
                  <Stethoscope className="w-4 h-4" />
                  <span>{t('common.doctors')}</span>
                </div>
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-8 space-y-3 no-scrollbar">
              {linkLoading ? (
                <div className="text-center py-10 text-gray-400">Loading...</div>
              ) : linkType === 'patient' ? (
                linkablePatients.length > 0 ? linkablePatients.map(p => (
                  <button
                    key={p.id}
                    onClick={() => handleLinkRecord(p.id)}
                    className="w-full flex items-center justify-between p-4 rounded-2xl border border-gray-100 dark:border-dark-border hover:border-blue-300 dark:hover:border-blue-700 hover:bg-blue-50/30 dark:hover:bg-blue-900/10 transition-all text-left group"
                  >
                    <div className="flex items-center space-x-4">
                      <div className="w-12 h-12 rounded-xl bg-indigo-50 dark:bg-indigo-900/30 flex items-center justify-center text-indigo-600 font-bold uppercase">
                        {p.name?.family?.[0] || 'P'}
                      </div>
                      <div>
                        <p className="font-bold text-brand-navy dark:text-dark-text">{p.name?.given?.join(' ')} {p.name?.family}</p>
                        <p className="text-xs text-gray-400">{p.gender} • {p.birth_date}</p>
                      </div>
                    </div>
                    <LinkIcon className="w-5 h-5 text-gray-300 group-hover:text-blue-500 transition-colors" />
                  </button>
                )) : (
                  <div className="text-center py-10 text-gray-400">{t('common.no_patients')}</div>
                )
              ) : (
                linkableDoctors.length > 0 ? linkableDoctors.map(d => (
                  <button
                    key={d.id}
                    onClick={() => handleLinkRecord(d.id)}
                    className="w-full flex items-center justify-between p-4 rounded-2xl border border-gray-100 dark:border-dark-border hover:border-blue-300 dark:hover:border-blue-700 hover:bg-blue-50/30 dark:hover:bg-blue-900/10 transition-all text-left group"
                  >
                    <div className="flex items-center space-x-4">
                      <div className="w-12 h-12 rounded-xl bg-blue-50 dark:bg-blue-900/30 flex items-center justify-center text-blue-600 font-bold uppercase">
                        <Stethoscope className="w-6 h-6" />
                      </div>
                      <div>
                        <p className="font-bold text-brand-navy dark:text-dark-text">Dr. {d.name}</p>
                        <p className="text-xs text-gray-400">{d.specialty}</p>
                      </div>
                    </div>
                    <LinkIcon className="w-5 h-5 text-gray-300 group-hover:text-blue-500 transition-colors" />
                  </button>
                )) : (
                  <div className="text-center py-10 text-gray-400">{t('common.no_doctors')}</div>
                )
              )}
            </div>

            <div className="p-8 bg-gray-50/50 dark:bg-dark-bg/50 border-t border-gray-100 dark:border-dark-border shrink-0">
               <button
                  onClick={() => setIsLinkModalOpen(false)}
                  className="w-full px-4 py-3 border border-gray-200 dark:border-dark-border rounded-xl hover:bg-white dark:hover:bg-dark-surface transition-colors font-bold text-gray-600 dark:text-dark-muted shadow-sm"
                >
                  {t('common.close')}
                </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default UserDetail;
