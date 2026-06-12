import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { getOrganization, deleteOrganization } from '../../services/organizationService';
import { getExaminations } from '../../services/examinationService';
import { Organization, Examination } from '../../types/clinical';
import { useUIStore } from '../../store/slices/uiSlice';
import { 
  Building2, Mail, Phone, MapPin, 
  Trash2, Edit2, ChevronRight, FileText, Calendar,
  ArrowLeft, Users, Building, Activity
} from 'lucide-react';
import { LoadingState } from '../../components/ui/LoadingState';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';

function OrganizationDetail() {
  const { t } = useTranslation();
  const { organizationId } = useParams<{ organizationId: string }>();
  const navigate = useNavigate();
  const showConfirmation = useUIStore(state => state.showConfirmation);
  
  const [organization, setOrganization] = useState<Organization | null>(null);
  const [examinations, setExaminations] = useState<Examination[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (organizationId) {
      loadData();
    }
  }, [organizationId]);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [orgData, allExams] = await Promise.all([
        getOrganization(organizationId!),
        getExaminations()
      ]);
      setOrganization(orgData);
      
      // Filter examinations associated with this organization
      const orgExams = allExams.filter((exam: Examination) => 
        (exam as any).organization_id === organizationId
      );
      setExaminations(orgExams);
    } catch (err) {
      console.error('Failed to load organization details:', err);
      setError('Failed to load organization details.');
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = () => {
    if (!organization) return;
    showConfirmation({
      title: t('organizations.delete_organization_title'),
      message: t('organizations.delete_organization_confirm', { name: organization.name }),
      confirmLabel: t('common.delete'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deleteOrganization(organization.id);
          navigate('/organizations');
        } catch (err) {
          console.error('Failed to delete organization:', err);
          alert(t('organizations.failed_delete'));
        }
      }
    });
  };

  if (loading) return <LoadingState showText message="Loading facility details..." />;
  
  if (error || !organization) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <Building2 className="w-16 h-16 text-gray-300 mb-4" />
        <h2 className="text-2xl font-bold text-gray-900 dark:text-dark-text">{error || 'Facility not found'}</h2>
        <button 
          onClick={() => navigate('/organizations')}
          className="mt-6 flex items-center space-x-2 px-6 py-2 bg-blue-600 text-white rounded-xl font-bold"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>Back to Facilities</span>
        </button>
      </div>
    );
  }

  const email = organization.telecom?.find(t => t.system === 'email')?.value;
  const phone = organization.telecom?.find(t => t.system === 'phone')?.value;
  const address = organization.address?.[0];

  return (
    <div className="max-w-6xl mx-auto space-y-8 pb-10">
      <PageHeader
        title={organization.name}
        subtitle={organization.type?.[0]?.coding?.[0]?.display || 'Medical Facility'}
        icon={<Building2 className="w-8 h-8" />}
        breadcrumbs={[
          { label: t('organizations.title'), path: '/organizations' }
        ]}
        showBackButton={true}
      />

      <StickyToolbar
        actions={
          <div className="flex items-center space-x-3">
            <button 
              onClick={() => navigate('/organizations', { state: { editingOrganization: organization } })}
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

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Left Column: Info Card */}
        <div className="lg:col-span-1 space-y-8">
          <div className="bg-white dark:bg-dark-surface rounded-3xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden">
            <div className="p-8">
              <div className="w-20 h-20 bg-indigo-50 dark:bg-indigo-900/30 rounded-2xl flex items-center justify-center text-indigo-600 mb-6 border border-indigo-100 dark:border-indigo-800 shadow-inner mx-auto">
                <Building className="w-10 h-10" />
              </div>
              
              <div className="text-center space-y-2 mb-8">
                <h2 className="text-2xl font-black text-[#1a2b4b] dark:text-dark-text">{organization.name}</h2>
                <p className="text-sm font-bold text-blue-600 dark:text-blue-400 uppercase tracking-widest">
                  {organization.type?.[0]?.coding?.[0]?.display || 'Medical Facility'}
                </p>
              </div>

              <div className="space-y-6">
                {address && (
                  <div className="flex items-start space-x-3">
                    <div className="p-2 bg-gray-50 dark:bg-dark-bg rounded-lg shrink-0">
                      <MapPin className="w-5 h-5 text-gray-400" />
                    </div>
                    <div>
                      <p className="text-[10px] font-black uppercase tracking-widest text-gray-400">{t('organizations.address')}</p>
                      <p className="font-bold text-gray-700 dark:text-dark-text text-sm">
                        {address.line?.[0]}<br />
                        {address.city}, {address.state} {address.postalCode}<br />
                        {address.country}
                      </p>
                    </div>
                  </div>
                )}

                {email && (
                  <div className="flex items-center space-x-3">
                    <div className="p-2 bg-gray-50 dark:bg-dark-bg rounded-lg shrink-0">
                      <Mail className="w-5 h-5 text-gray-400" />
                    </div>
                    <div>
                      <p className="text-[10px] font-black uppercase tracking-widest text-gray-400">{t('organizations.email')}</p>
                      <p className="font-bold text-gray-700 dark:text-dark-text text-sm truncate max-w-[180px]">{email}</p>
                    </div>
                  </div>
                )}

                {phone && (
                  <div className="flex items-center space-x-3">
                    <div className="p-2 bg-gray-50 dark:bg-dark-bg rounded-lg shrink-0">
                      <Phone className="w-5 h-5 text-gray-400" />
                    </div>
                    <div>
                      <p className="text-[10px] font-black uppercase tracking-widest text-gray-400">{t('organizations.phone')}</p>
                      <p className="font-bold text-gray-700 dark:text-dark-text text-sm">{phone}</p>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Medical Staff Summary */}
          <div className="bg-white dark:bg-dark-surface rounded-3xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden p-6">
            <div className="flex items-center justify-between mb-6">
              <div className="flex items-center space-x-2">
                <Users className="w-5 h-5 text-blue-500" />
                <h3 className="font-bold">{t('organizations.doctors_in_facility')}</h3>
              </div>
              <span className="bg-blue-50 dark:bg-blue-900/30 text-blue-600 px-2 py-1 rounded-lg text-xs font-bold">
                {organization.doctors?.length || 0}
              </span>
            </div>
            
            <div className="space-y-4">
              {!organization.doctors || organization.doctors.length === 0 ? (
                <p className="text-sm text-gray-500 text-center py-4">{t('organizations.no_doctors_linked')}</p>
              ) : (
                organization.doctors.map(doctor => (
                  <div 
                    key={doctor.id}
                    onClick={() => navigate(`/doctors/${doctor.id}`)}
                    className="flex items-center space-x-3 p-2 hover:bg-gray-50 dark:hover:bg-dark-bg rounded-xl transition-colors cursor-pointer group"
                  >
                    <div className="w-10 h-10 bg-blue-50 dark:bg-blue-900/20 rounded-full flex items-center justify-center text-blue-600 shrink-0">
                      <Users className="w-5 h-5" />
                    </div>
                    <div className="flex-1 truncate">
                      <p className="text-sm font-bold group-hover:text-blue-600 transition-colors">Dr. {doctor.name}</p>
                      <p className="text-[10px] text-gray-500 uppercase font-black tracking-widest">{doctor.specialty}</p>
                    </div>
                    <ChevronRight className="w-4 h-4 text-gray-300 group-hover:text-blue-500 transition-colors" />
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        {/* Right Column: Activity & Departments */}
        <div className="lg:col-span-2 space-y-8">
          {/* Clinical Activity */}
          <div className="bg-white dark:bg-dark-surface rounded-3xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden">
            <div className="px-8 py-6 border-b border-gray-50 dark:border-dark-border flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <div className="p-2 bg-indigo-50 dark:bg-indigo-900/20 rounded-xl">
                  <Activity className="w-5 h-5 text-indigo-600" />
                </div>
                <h2 className="text-xl font-bold text-[#1a2b4b] dark:text-dark-text tracking-tight">{t('organizations.clinical_visits')}</h2>
              </div>
              <span className="px-4 py-1.5 bg-gray-100 dark:bg-dark-bg text-gray-500 dark:text-dark-muted text-xs font-black uppercase tracking-widest rounded-full">
                {examinations.length} Visits
              </span>
            </div>

            <div className="p-0">
              {examinations.length === 0 ? (
                <div className="p-12 text-center">
                  <div className="w-16 h-16 bg-gray-50 dark:bg-dark-bg rounded-full flex items-center justify-center mx-auto mb-4 border border-gray-100 dark:border-dark-border">
                    <FileText className="w-8 h-8 text-gray-300" />
                  </div>
                  <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">No visits recorded</h3>
                  <p className="text-gray-500 mt-1 max-w-xs mx-auto">No clinical examinations have been recorded at this facility yet.</p>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="bg-gray-50/50 dark:bg-dark-bg/30">
                        <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest">Date</th>
                        <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest">Patient</th>
                        <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest">Category</th>
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
                            <span className="text-sm font-medium text-gray-700 dark:text-dark-text">
                              {/* In a real scenario, we'd have the patient name here */}
                              Patient ID: {exam.patient_id.substring(0, 8)}...
                            </span>
                          </td>
                          <td className="px-8 py-5">
                            <span className="px-3 py-1 bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 text-[10px] font-black uppercase tracking-widest rounded-full border border-blue-100 dark:border-blue-800/50">
                              {exam.category || 'General'}
                            </span>
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

          {/* Departments Section */}
          <div className="bg-white dark:bg-dark-surface rounded-3xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden">
            <div className="px-8 py-6 border-b border-gray-50 dark:border-dark-border flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <div className="p-2 bg-purple-50 dark:bg-purple-900/20 rounded-xl">
                  <Building className="w-5 h-5 text-purple-600" />
                </div>
                <h2 className="text-xl font-bold text-[#1a2b4b] dark:text-dark-text tracking-tight">{t('organizations.departments')}</h2>
              </div>
              <span className="px-4 py-1.5 bg-gray-100 dark:bg-dark-bg text-gray-500 dark:text-dark-muted text-xs font-black uppercase tracking-widest rounded-full">
                {organization.departments?.length || 0} Units
              </span>
            </div>
            
            <div className="p-8">
              {!organization.departments || organization.departments.length === 0 ? (
                <div className="text-center py-8">
                  <p className="text-sm text-gray-500 italic">No departments or units have been defined for this facility.</p>
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {organization.departments.map(dept => (
                    <div 
                      key={dept.id}
                      onClick={() => navigate(`/organizations/${dept.id}`)}
                      className="p-4 border border-gray-100 dark:border-dark-border rounded-2xl hover:border-blue-200 dark:hover:border-blue-900 hover:shadow-md transition-all cursor-pointer group"
                    >
                      <h4 className="font-bold text-gray-900 dark:text-dark-text group-hover:text-blue-600 transition-colors">{dept.name}</h4>
                      <p className="text-xs text-gray-500 mt-1 uppercase tracking-widest font-black">
                        {dept.type?.[0]?.coding?.[0]?.display || 'Department'}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default OrganizationDetail;
