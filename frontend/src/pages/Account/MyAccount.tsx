import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  User as UserIcon,
  Link as LinkIcon,
  Stethoscope,
  ArrowLeft,
  Settings,
  Activity,
  ChevronRight,
  ShieldCheck,
  KeyRound,
  UserCircle,
} from 'lucide-react';
import { getCurrentUser, User } from '../../services/userService';
import { listPatients } from '../../services/patientService';
import { listDoctors } from '../../services/doctorService';
import { PageHeader } from '../../components/ui/PageHeader';
import { LoadingState } from '../../components/ui/LoadingState';

function MyAccount() {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const [user, setUser] = useState<User | null>(null);
  const [linkedPatients, setLinkedPatients] = useState<any[]>([]);
  const [linkedDoctors, setLinkedDoctors] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const userData = await getCurrentUser();
      setUser(userData);

      const [patientsRes, doctorsData] = await Promise.all([
        listPatients(undefined, 50, 0, userData.id),
        listDoctors(userData.id),
      ]);

      setLinkedPatients(patientsRes.items || []);
      setLinkedDoctors(doctorsData || []);
    } catch (err) {
      console.error('Failed to load account details:', err);
      setError(t('account.load_error', 'Failed to load account details.'));
    } finally {
      setLoading(false);
    }
  };

  if (loading) return <LoadingState showText message={t('account.loading', 'Loading your profile...')} />;

  if (error || !user) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <UserIcon className="w-16 h-16 text-gray-300 mb-4" />
        <h2 className="text-2xl font-bold text-gray-900 dark:text-dark-text">
          {error || t('account.not_found', 'Account not found')}
        </h2>
        <button
          onClick={() => navigate('/')}
          className="mt-6 flex items-center space-x-2 px-6 py-2 bg-blue-600 text-white rounded-xl font-bold"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>{t('common.back')}</span>
        </button>
      </div>
    );
  }

  const roleLabel =
    user.role === 'SYSTEM_ADMIN' ? t('admin.role_system_admin') :
    user.role === 'ADMIN' ? t('admin.role_admin') :
    user.role === 'MANAGER' ? t('admin.role_manager') :
    t('admin.role_user');

  return (
    <div className="max-w-6xl mx-auto space-y-8 pb-10">
      <PageHeader
        title={t('account.title', 'My Account')}
        subtitle={t('account.subtitle', 'Your identity, linked records, and access')}
        icon={<UserCircle className="w-8 h-8" />}
        showBackButton={true}
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Left Column: Profile Summary + Quick Links */}
        <div className="lg:col-span-1 space-y-6">
          <div className="bg-white dark:bg-dark-surface rounded-3xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden p-8">
            <div className="w-24 h-24 bg-gradient-to-br from-blue-500 to-indigo-600 rounded-3xl flex items-center justify-center text-white text-3xl font-black shadow-lg mx-auto mb-6">
              {user.email[0].toUpperCase()}
            </div>

            <div className="text-center space-y-1 mb-8">
              <h2 className="text-xl font-bold text-[#1a2b4b] dark:text-dark-text break-all">{user.email}</h2>
              <span className="inline-block px-3 py-1 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 text-[10px] font-black uppercase tracking-widest rounded-full">
                {roleLabel}
              </span>
            </div>

            <div className="space-y-4 pt-6 border-t border-gray-50 dark:border-dark-border">
              <div className="flex items-center space-x-3">
                <div className="p-2 bg-gray-50 dark:bg-dark-bg rounded-lg shrink-0">
                  <ShieldCheck className="w-4 h-4 text-gray-400" />
                </div>
                <div>
                  <p className="text-[9px] font-black uppercase tracking-widest text-gray-400">{t('admin.status')}</p>
                  <p className="font-bold text-sm text-green-600 uppercase">{t('admin.active')}</p>
                </div>
              </div>
            </div>
          </div>

          {/* Quick Links */}
          <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-6">
            <h3 className="font-bold text-gray-900 dark:text-dark-text mb-4">{t('account.quick_links', 'Quick Links')}</h3>
            <div className="space-y-2">
              <button
                onClick={() => navigate('/settings')}
                className="w-full flex items-center justify-between p-3 bg-gray-50 dark:bg-dark-bg hover:bg-gray-100 dark:hover:bg-dark-border rounded-xl transition-colors group"
              >
                <div className="flex items-center space-x-3">
                  <Settings className="w-4 h-4 text-gray-400 group-hover:text-blue-500" />
                  <span className="text-sm font-semibold">{t('common.settings')}</span>
                </div>
                <ChevronRight className="w-4 h-4 text-gray-300" />
              </button>

              <button
                onClick={() => navigate('/settings/ai-config')}
                className="w-full flex items-center justify-between p-3 bg-gray-50 dark:bg-dark-bg hover:bg-gray-100 dark:hover:bg-dark-border rounded-xl transition-colors group"
              >
                <div className="flex items-center space-x-3">
                  <KeyRound className="w-4 h-4 text-gray-400 group-hover:text-blue-500" />
                  <span className="text-sm font-semibold">{t('account.ai_keys', 'AI Configuration')}</span>
                </div>
                <ChevronRight className="w-4 h-4 text-gray-300" />
              </button>

              <button
                onClick={() => navigate('/settings/integrations')}
                className="w-full flex items-center justify-between p-3 bg-gray-50 dark:bg-dark-bg hover:bg-gray-100 dark:hover:bg-dark-border rounded-xl transition-colors group"
              >
                <div className="flex items-center space-x-3">
                  <LinkIcon className="w-4 h-4 text-gray-400 group-hover:text-blue-500" />
                  <span className="text-sm font-semibold">{t('common.integrations')}</span>
                </div>
                <ChevronRight className="w-4 h-4 text-gray-300" />
              </button>
            </div>
          </div>
        </div>

        {/* Right Column: Linked Records + Metadata */}
        <div className="lg:col-span-2 space-y-8">
          {/* Linked Clinical Profiles */}
          <div className="bg-white dark:bg-dark-surface rounded-3xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden">
            <div className="px-8 py-6 border-b border-gray-100 dark:border-dark-border flex items-center justify-between bg-gray-50/30 dark:bg-dark-bg/20">
              <div className="flex items-center space-x-3">
                <div className="p-2 bg-indigo-50 dark:bg-indigo-900/20 rounded-xl">
                  <LinkIcon className="w-5 h-5 text-indigo-600" />
                </div>
                <h3 className="text-lg font-bold text-[#1a2b4b] dark:text-dark-text tracking-tight">
                  {t('account.linked_profiles', 'Your Linked Records')}
                </h3>
              </div>
            </div>

            <div className="p-8 space-y-8">
              {/* Linked Patients */}
              <div>
                <h4 className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-4 flex items-center">
                  <UserIcon className="w-3 h-3 mr-2" /> {t('account.linked_patients', 'Patient Profiles')}
                  <span className="ml-2 text-blue-500">({linkedPatients.length})</span>
                </h4>
                {linkedPatients.length === 0 ? (
                  <div className="p-6 bg-gray-50 dark:bg-dark-bg/50 rounded-2xl border border-dashed border-gray-200 dark:border-dark-border text-center">
                    <p className="text-sm text-gray-500 italic">
                      {t('account.no_linked_patients', 'You have no patient records linked to your account. Contact an admin if you need to be linked to one.')}
                    </p>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {linkedPatients.map(patient => (
                      <div
                        key={patient.id}
                        className="flex items-center justify-between p-4 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-2xl hover:border-blue-500 hover:shadow-md transition-all group cursor-pointer"
                        onClick={() => navigate(`/patients/${patient.id}`)}
                      >
                        <div className="flex items-center space-x-4">
                          <div className="w-12 h-12 rounded-xl bg-blue-50 dark:bg-blue-900/30 flex items-center justify-center text-blue-600 font-bold">
                            {patient.name?.family?.[0] || patient.name?.text?.[0] || 'P'}
                          </div>
                          <div>
                            <p className="font-bold text-[#1a2b4b] dark:text-dark-text">
                              {patient.name?.given?.join(' ')} {patient.name?.family || patient.name?.text}
                            </p>
                            <p className="text-[10px] text-gray-500 uppercase font-black tracking-widest">
                              {t('account.mrn')}: {patient.mrn || 'N/A'}
                            </p>
                          </div>
                        </div>
                        <ChevronRight className="w-5 h-5 text-gray-300 group-hover:text-blue-500 transition-colors" />
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Linked Doctors */}
              <div>
                <h4 className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-4 flex items-center">
                  <Stethoscope className="w-3 h-3 mr-2" /> {t('account.linked_doctors', 'Professional Profiles')}
                  <span className="ml-2 text-emerald-500">({linkedDoctors.length})</span>
                </h4>
                {linkedDoctors.length === 0 ? (
                  <div className="p-6 bg-gray-50 dark:bg-dark-bg/50 rounded-2xl border border-dashed border-gray-200 dark:border-dark-border text-center">
                    <p className="text-sm text-gray-500 italic">
                      {t('account.no_linked_doctors', 'You have no professional doctor profiles linked to your account.')}
                    </p>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {linkedDoctors.map(doctor => (
                      <div
                        key={doctor.id}
                        className="flex items-center justify-between p-4 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-2xl hover:border-blue-500 hover:shadow-md transition-all group cursor-pointer"
                        onClick={() => navigate(`/doctors/${doctor.id}`)}
                      >
                        <div className="flex items-center space-x-4">
                          <div className="w-12 h-12 rounded-xl bg-emerald-50 dark:bg-emerald-900/30 flex items-center justify-center text-emerald-600">
                            <Stethoscope className="w-6 h-6" />
                          </div>
                          <div>
                            <p className="font-bold text-[#1a2b4b] dark:text-dark-text">Dr. {doctor.name}</p>
                            <p className="text-[10px] text-gray-500 uppercase font-black tracking-widest">
                              {doctor.specialty || t('account.general_practitioner', 'General Practitioner')}
                            </p>
                          </div>
                        </div>
                        <ChevronRight className="w-5 h-5 text-gray-300 group-hover:text-blue-500 transition-colors" />
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
              <Activity className="w-5 h-5 text-indigo-500" />
              <h3 className="font-bold text-gray-900 dark:text-dark-text">{t('account.metadata', 'Account Metadata')}</h3>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
              <div className="p-4 bg-gray-50/50 dark:bg-dark-bg/30 rounded-2xl">
                <p className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1">{t('account.user_id', 'User Identifier')}</p>
                <p className="font-mono text-xs break-all">{user.id}</p>
              </div>
              <div className="p-4 bg-gray-50/50 dark:bg-dark-bg/30 rounded-2xl">
                <p className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1">{t('account.tenant_id', 'Tenant ID')}</p>
                <p className="font-mono text-xs break-all">{user.tenant_id}</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default MyAccount;
