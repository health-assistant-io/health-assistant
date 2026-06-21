import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { 
  Users, 
  UserPlus, 
  Shield, 
  User, 
  Trash2, 
  Link as LinkIcon, 
  X, 
  Save,
  CheckCircle2,
  Stethoscope,
  Eye
} from 'lucide-react';
import { listUsers, updateUser, deleteUser, createUser, User as UserType, UserRole } from '../../services/userService';
import { listPatients } from '../../services/patientService';
import { listDoctors } from '../../services/doctorService';
import { useUIStore } from '../../store/slices/uiSlice';
import { useAuthStore } from '../../store/slices/authSlice';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';

const ROLE_OPTIONS: { value: UserRole; label: string; color: string }[] = [
  { value: 'SYSTEM_ADMIN', label: 'admin.role_system_admin', color: 'text-purple-600 bg-purple-50 dark:bg-purple-900/20' },
  { value: 'ADMIN', label: 'admin.role_admin', color: 'text-red-600 bg-red-50 dark:bg-red-900/20' },
  { value: 'MANAGER', label: 'admin.role_manager', color: 'text-blue-600 bg-blue-50 dark:bg-blue-900/20' },
  { value: 'USER', label: 'admin.role_user', color: 'text-gray-600 bg-gray-50 dark:bg-gray-700/20' }
];

function UserManagement() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [users, setUsers] = useState<UserType[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRoleModalOpen, setIsRoleModalOpen] = useState(false);
  const [isLinkModalOpen, setIsLinkModalOpen] = useState(false);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [selectedUser, setSelectedUser] = useState<UserType | null>(null);
  const [targetRole, setTargetRole] = useState<UserRole>('USER');
  
  const [createFormData, setCreateFormData] = useState({
    email: '',
    password: '',
    role: 'USER' as UserRole
  });
  
  const [patients, setPatients] = useState<any[]>([]);
  const [doctors, setDoctors] = useState<any[]>([]);
  const [linkType, setLinkType] = useState<'patient' | 'doctor'>('patient');
  
  const showConfirmation = useUIStore(state => state.showConfirmation);
  const currentUser = useAuthStore(state => state.user);

  const fetchUsers = async () => {
    try {
      const data = await listUsers();
      setUsers(data);
    } catch (err) {
      console.error('Failed to fetch users:', err);
    }
  };

  useEffect(() => {
    const loadData = async () => {
      setIsLoading(true);
      await fetchUsers();
      setIsLoading(false);
    };
    loadData();
  }, []);

  const handleOpenRoleModal = (user: UserType) => {
    setSelectedUser(user);
    setTargetRole(user.role);
    setIsRoleModalOpen(true);
  };

  const handleUpdateRole = async () => {
    if (!selectedUser) return;
    try {
      await updateUser(selectedUser.id, { role: targetRole });
      await fetchUsers();
      setIsRoleModalOpen(false);
    } catch (err) {
      console.error('Failed to update role:', err);
    }
  };

  const handleCreateUser = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const payload: any = { ...createFormData };
      if (currentUser?.tenant_id) {
        payload.tenant_id = currentUser.tenant_id;
      }
      
      await createUser(payload);
      await fetchUsers();
      setIsCreateModalOpen(false);
      setCreateFormData({ email: '', password: '', role: 'USER' });
    } catch (err) {
      console.error('Failed to create user:', err);
      alert('Failed to create user. Email might already be taken.');
    }
  };

  const handleDeleteUser = (user: UserType) => {
    if (user.id === currentUser?.id) {
      alert(t('admin.cannot_delete_self'));
      return;
    }

    showConfirmation({
      title: t('admin.delete_user_title'),
      message: t('admin.delete_user_confirm', { email: user.email }),
      confirmLabel: t('common.delete'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deleteUser(user.id);
          fetchUsers();
        } catch (err) {
          console.error('Failed to delete user:', err);
        }
      }
    });
  };

  const handleOpenLinkModal = async (user: UserType) => {
    setSelectedUser(user);
    setIsLinkModalOpen(true);
    
    // Pre-fetch patients and doctors
    try {
      const [patientsData, doctorsData] = await Promise.all([
        listPatients(),
        listDoctors()
      ]);
      setPatients(patientsData.items);
      setDoctors(doctorsData);
    } catch (err) {
      console.error('Failed to fetch patients/doctors for linking:', err);
    }
  };

  const handleLinkRecord = async (recordId: string) => {
    if (!selectedUser) return;
    
    try {
      // Logic for linking depends on if we update the user with profile ID
      // or update the profile with user ID.
      // Based on previous plan, we add user_id to Doctor/Patient models.
      
      if (linkType === 'patient') {
        const { updatePatient } = await import('../../services/patientService');
        await updatePatient(recordId, { user_id: selectedUser.id } as any);
      } else {
        const { updateDoctor } = await import('../../services/doctorService');
        await updateDoctor(recordId, { user_id: selectedUser.id } as any);
      }
      
      alert(t('admin.link_success'));
      setIsLinkModalOpen(false);
    } catch (err) {
      console.error('Failed to link record:', err);
      alert(t('admin.link_failed'));
    }
  };

  if (isLoading) {
    return <div className="flex items-center justify-center h-full text-gray-500">{t('admin.loading_users')}</div>;
  }

  return (
    <div className="space-y-6 pb-20">
      <PageHeader
        title={t('admin.user_management')}
        subtitle={t('admin.user_management_subtitle')}
        icon={<Users className="w-8 h-8" />}
      />

      <StickyToolbar
        actions={
          <button 
            onClick={() => setIsCreateModalOpen(true)}
            className="flex items-center space-x-2 px-6 py-2.5 bg-blue-600 text-white rounded-xl hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none font-bold active:scale-95"
          >
            <UserPlus className="w-4 h-4" />
            <span>{t('admin.invite_user')}</span>
          </button>
        }
      />

      <div className="bg-white dark:bg-dark-surface rounded-3xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-gray-50/50 dark:bg-dark-bg/50 border-b border-gray-100 dark:border-dark-border">
                <th className="px-6 py-4 text-xs font-black uppercase tracking-wider text-gray-400">{t('admin.user')}</th>
                <th className="px-6 py-4 text-xs font-black uppercase tracking-wider text-gray-400">{t('admin.role')}</th>
                <th className="px-6 py-4 text-xs font-black uppercase tracking-wider text-gray-400">{t('admin.status')}</th>
                <th className="px-6 py-4 text-xs font-black uppercase tracking-wider text-gray-400 text-right">{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50 dark:divide-dark-border">
              {users.map((user) => (
                <tr key={user.id} className="hover:bg-gray-50/30 dark:hover:bg-dark-bg/30 transition-colors group">
                  <td className="px-6 py-4">
                    <div className="flex items-center space-x-3">
                      <div className="w-10 h-10 rounded-full bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center text-white font-bold shadow-sm">
                        {user.email[0].toUpperCase()}
                      </div>
                      <div>
                        <p className="font-bold text-[#1a2b4b] dark:text-dark-text">{user.email}</p>
                        <p className="text-[10px] text-gray-400 font-mono uppercase tracking-tighter">{user.id.substring(0, 8)}...</p>
                      </div>
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <span className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-black uppercase tracking-wide ${
                      ROLE_OPTIONS.find(r => r.value === user.role)?.color || 'bg-gray-100 text-gray-600'
                    }`}>
                      {t(ROLE_OPTIONS.find(r => r.value === user.role)?.label || 'USER')}
                    </span>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex items-center space-x-2">
                      <CheckCircle2 className="w-4 h-4 text-green-500" />
                      <span className="text-sm text-gray-500 dark:text-dark-muted font-medium">{t('admin.active')}</span>
                    </div>
                  </td>
                  <td className="px-6 py-4 text-right">
                    <div className="flex items-center justify-end space-x-1 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button 
                        onClick={() => {
                          const basePath = location.pathname.startsWith('/admin/system') ? '/admin/system/users' : '/admin/tenant/users';
                          navigate(`${basePath}/${user.id}`);
                        }}
                        className="p-2 text-gray-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-xl transition-all"
                        title={t('common.view_details')}
                      >
                        <Eye className="w-4 h-4" />
                      </button>
                      <button 
                        onClick={() => handleOpenLinkModal(user)}
                        className="p-2 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 rounded-xl transition-all"
                        title={t('admin.link_record')}
                      >
                        <LinkIcon className="w-4 h-4" />
                      </button>
                      <button 
                        onClick={() => handleOpenRoleModal(user)}
                        className="p-2 text-gray-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-xl transition-all"
                        title={t('admin.change_role')}
                      >
                        <Shield className="w-4 h-4" />
                      </button>
                      <button 
                        onClick={() => handleDeleteUser(user)}
                        className="p-2 text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-xl transition-all"
                        title={t('common.delete')}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Role Management Modal */}
      {isRoleModalOpen && selectedUser && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-[1000] p-4 animate-in fade-in duration-200">
          <div className="bg-white dark:bg-dark-surface rounded-3xl w-full max-w-md shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200">
            <div className="px-8 py-6 border-b border-gray-100 dark:border-dark-border flex justify-between items-center bg-gray-50/50 dark:bg-dark-bg/50">
              <div>
                <h2 className="text-xl font-bold text-[#1a2b4b] dark:text-dark-text">{t('admin.change_role')}</h2>
                <p className="text-sm text-gray-500 dark:text-dark-muted">{selectedUser.email}</p>
              </div>
              <button onClick={() => setIsRoleModalOpen(false)} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-border rounded-full transition-colors text-gray-400">
                <X className="w-5 h-5" />
              </button>
            </div>
            
            <div className="p-8 space-y-4">
              <div className="grid grid-cols-1 gap-3">
                {ROLE_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    onClick={() => setTargetRole(option.value)}
                    className={`flex items-center justify-between p-4 rounded-2xl border-2 transition-all text-left ${
                      targetRole === option.value
                        ? 'border-blue-500 bg-blue-50/50 dark:bg-blue-900/20'
                        : 'border-gray-100 dark:border-dark-border hover:border-gray-200 dark:hover:border-gray-600'
                    }`}
                  >
                    <div className="flex items-center space-x-3">
                      <div className={`p-2 rounded-lg ${option.color}`}>
                        <Shield className="w-5 h-5" />
                      </div>
                      <div>
                        <p className="font-bold text-[#1a2b4b] dark:text-dark-text">{t(option.label)}</p>
                        <p className="text-[10px] text-gray-500 uppercase tracking-widest font-black">Scope: {option.value.includes('SYSTEM') ? 'Global' : 'Tenant'}</p>
                      </div>
                    </div>
                    {targetRole === option.value && (
                      <div className="w-6 h-6 rounded-full bg-blue-500 flex items-center justify-center text-white">
                        <CheckCircle2 className="w-4 h-4" />
                      </div>
                    )}
                  </button>
                ))}
              </div>

              <div className="pt-6 flex space-x-3">
                <button
                  onClick={() => setIsRoleModalOpen(false)}
                  className="flex-1 px-4 py-3 border border-gray-200 dark:border-dark-border rounded-xl hover:bg-gray-50 dark:hover:bg-dark-border transition-colors font-bold text-gray-600 dark:text-dark-muted"
                >
                  {t('common.cancel')}
                </button>
                <button
                  onClick={handleUpdateRole}
                  className="flex-1 px-4 py-3 bg-[#0088CC] text-white rounded-xl hover:bg-[#0077B3] transition-all font-bold flex items-center justify-center space-x-2 shadow-lg shadow-blue-200 dark:shadow-none"
                >
                  <Save className="w-4 h-4" />
                  <span>{t('common.save')}</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Linking Modal */}
      {isLinkModalOpen && selectedUser && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-[1000] p-4 animate-in fade-in duration-200">
          <div className="bg-white dark:bg-dark-surface rounded-3xl w-full max-w-2xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200 flex flex-col max-h-[85vh]">
            <div className="px-8 py-6 border-b border-gray-100 dark:border-dark-border flex justify-between items-center bg-gray-50/50 dark:bg-dark-bg/50 shrink-0">
              <div>
                <h2 className="text-xl font-bold text-[#1a2b4b] dark:text-dark-text">{t('admin.link_identity')}</h2>
                <p className="text-sm text-gray-500 dark:text-dark-muted">{t('admin.link_identity_help', { email: selectedUser.email })}</p>
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
                  <User className="w-4 h-4" />
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
              {linkType === 'patient' ? (
                patients.length > 0 ? patients.map(p => (
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
                        <p className="font-bold text-[#1a2b4b] dark:text-dark-text">{p.name?.given?.join(' ')} {p.name?.family}</p>
                        <p className="text-xs text-gray-400">{p.gender} • {p.birth_date}</p>
                      </div>
                    </div>
                    <LinkIcon className="w-5 h-5 text-gray-300 group-hover:text-blue-500 transition-colors" />
                  </button>
                )) : (
                  <div className="text-center py-10 text-gray-400">{t('common.no_patients')}</div>
                )
              ) : (
                doctors.length > 0 ? doctors.map(d => (
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
                        <p className="font-bold text-[#1a2b4b] dark:text-dark-text">Dr. {d.name}</p>
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

      {/* Create User Modal */}
      {isCreateModalOpen && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-[1000] p-4 animate-in fade-in duration-200">
          <div className="bg-white dark:bg-dark-surface rounded-3xl w-full max-w-md shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200">
            <div className="px-8 py-6 border-b border-gray-100 dark:border-dark-border flex justify-between items-center bg-gray-50/50 dark:bg-dark-bg/50">
              <h2 className="text-xl font-bold text-[#1a2b4b] dark:text-dark-text">{t('admin.invite_user')}</h2>
              <button onClick={() => setIsCreateModalOpen(false)} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-border rounded-full transition-colors text-gray-400">
                <X className="w-5 h-5" />
              </button>
            </div>
            
            <form onSubmit={handleCreateUser} className="p-8 space-y-4">
              <div>
                <label className="block text-xs font-black text-gray-400 uppercase tracking-widest mb-2">Email Address</label>
                <input
                  type="email"
                  required
                  className="w-full px-5 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-2xl outline-none focus:ring-2 focus:ring-blue-500/20 dark:text-dark-text"
                  value={createFormData.email}
                  onChange={(e) => setCreateFormData({ ...createFormData, email: e.target.value })}
                  placeholder="e.g. spouse@example.com"
                />
              </div>

              <div>
                <label className="block text-xs font-black text-gray-400 uppercase tracking-widest mb-2">Temporary Password</label>
                <input
                  type="password"
                  required
                  minLength={8}
                  className="w-full px-5 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-2xl outline-none focus:ring-2 focus:ring-blue-500/20 dark:text-dark-text"
                  value={createFormData.password}
                  onChange={(e) => setCreateFormData({ ...createFormData, password: e.target.value })}
                />
              </div>

              <div>
                <label className="block text-xs font-black text-gray-400 uppercase tracking-widest mb-2">Role</label>
                <select
                  className="w-full px-5 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-2xl outline-none focus:ring-2 focus:ring-blue-500/20 dark:text-dark-text"
                  value={createFormData.role}
                  onChange={(e) => setCreateFormData({ ...createFormData, role: e.target.value as UserRole })}
                >
                  {ROLE_OPTIONS.filter(r => r.value !== 'SYSTEM_ADMIN').map(r => (
                    <option key={r.value} value={r.value}>{t(r.label)}</option>
                  ))}
                </select>
              </div>

              <div className="pt-6 flex space-x-3">
                <button
                  type="button"
                  onClick={() => setIsCreateModalOpen(false)}
                  className="flex-1 px-4 py-3 border border-gray-200 dark:border-dark-border rounded-xl hover:bg-gray-50 dark:hover:bg-dark-border transition-colors font-bold text-gray-600 dark:text-dark-muted"
                >
                  {t('common.cancel')}
                </button>
                <button
                  type="submit"
                  className="flex-1 px-4 py-3 bg-[#0088CC] text-white rounded-xl hover:bg-[#0077B3] transition-all font-bold flex items-center justify-center space-x-2 shadow-lg shadow-blue-200 dark:shadow-none"
                >
                  <Save className="w-4 h-4" />
                  <span>{t('common.save')}</span>
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

export default UserManagement;
