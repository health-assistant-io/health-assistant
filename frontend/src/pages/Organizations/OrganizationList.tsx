import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Plus, Building2, Mail, Phone, MapPin, Trash2, Edit2, Building } from 'lucide-react';
import { listOrganizations, createOrganization, updateOrganization, deleteOrganization } from '../../services/organizationService';
import { listDoctors } from '../../services/doctorService';
import { Organization, Doctor } from '../../types/clinical';
import { useUIStore } from '../../store/slices/uiSlice';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { useCreateIntent } from '../../hooks/useCreateIntent';
import { FormModal } from '../../components/ui/FormModal';

const ORGANIZATION_TYPES = [
  { value: 'prov', label: 'organizations.hospital' },
  { value: 'dept', label: 'organizations.clinic' },
  { value: 'team', label: 'organizations.private_practice' },
  { value: 'govt', label: 'organizations.diagnostic_center' },
  { value: 'ins', label: 'organizations.insurance' },
  { value: 'other', label: 'organizations.other' }
];

function OrganizationList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [doctors, setDoctors] = useState<Doctor[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingOrganization, setEditingOrganization] = useState<Organization | null>(null);
  const showConfirmation = useUIStore(state => state.showConfirmation);
  
  // Form state
  const [formData, setFormData] = useState({
    name: '',
    type: 'prov',
    email: '',
    phone: '',
    address: {
      line: '',
      city: '',
      state: '',
      postalCode: '',
      country: ''
    },
    doctor_ids: [] as string[]
  });

  const fetchOrganizations = async () => {
    try {
      const data = await listOrganizations();
      setOrganizations(data);
    } catch (err) {
      console.error('Failed to fetch organizations:', err);
    }
  };

  const fetchDoctors = async () => {
    try {
      const data = await listDoctors();
      setDoctors(data);
    } catch (err) {
      console.error('Failed to fetch doctors:', err);
    }
  };

  useEffect(() => {
    const loadData = async () => {
      setIsLoading(true);
      await Promise.all([fetchOrganizations(), fetchDoctors()]);
      setIsLoading(false);
    };
    loadData();
  }, []);

  const handleOpenModal = (org: Organization | null = null) => {
    if (org) {
      setEditingOrganization(org);
      const typeValue = org.type && org.type.length > 0 ? org.type[0].coding?.[0]?.code || 'prov' : 'prov';
      const email = org.telecom?.find(t => t.system === 'email')?.value || '';
      const phone = org.telecom?.find(t => t.system === 'phone')?.value || '';
      const addr = org.address?.[0] || {};
      
      setFormData({
        name: org.name,
        type: typeValue,
        email,
        phone,
        address: {
          line: addr.line?.[0] || '',
          city: addr.city || '',
          state: addr.state || '',
          postalCode: addr.postalCode || '',
          country: addr.country || ''
        },
        doctor_ids: (org as any).doctors?.map((d: any) => d.id) || []
      });
    } else {
      setEditingOrganization(null);
      setFormData({ 
        name: '', 
        type: 'prov', 
        email: '', 
        phone: '', 
        address: { line: '', city: '', state: '', postalCode: '', country: '' },
        doctor_ids: [] 
      });
    }
    setIsModalOpen(true);
  };

  // Open the create modal automatically when arrived via ?new=organization
  useCreateIntent(() => handleOpenModal(), 'organization');

  const handleCloseModal = () => {
    setIsModalOpen(false);
    setEditingOrganization(null);
  };

  const handleSubmit = async () => {
    // Construct FHIR-like objects for the API
    const payload = {
      name: formData.name,
      active: true,
      type: [{
        coding: [{
          system: 'http://terminology.hl7.org/CodeSystem/organization-type',
          code: formData.type,
          display: t(ORGANIZATION_TYPES.find(ot => ot.value === formData.type)?.label || 'Other') as string
        }]
      }],
      telecom: [
        { system: 'phone', value: formData.phone },
        { system: 'email', value: formData.email }
      ],
      address: [{
        line: [formData.address.line],
        city: formData.address.city,
        state: formData.address.state,
        postalCode: formData.address.postalCode,
        country: formData.address.country
      }],
      doctor_ids: formData.doctor_ids
    };

    try {
      if (editingOrganization) {
        await updateOrganization(editingOrganization.id, payload);
      } else {
        await createOrganization(payload);
      }
      fetchOrganizations();
      handleCloseModal();
    } catch (err) {
      console.error('Failed to save organization:', err);
    }
  };

  const handleDelete = (id: string, name: string) => {
    showConfirmation({
      title: t('organizations.delete_organization_title'),
      message: t('organizations.delete_organization_confirm', { name }),
      confirmLabel: t('common.delete'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deleteOrganization(id);
          fetchOrganizations();
        } catch (err) {
          console.error('Failed to delete organization:', err);
          alert(t('organizations.failed_delete'));
        }
      }
    });
  };

  const toggleDoctorSelection = (doctorId: string) => {
    setFormData(prev => ({
      ...prev,
      doctor_ids: prev.doctor_ids.includes(doctorId)
        ? prev.doctor_ids.filter(id => id !== doctorId)
        : [...prev.doctor_ids, doctorId]
    }));
  };

  if (isLoading) {
    return <div className="flex items-center justify-center h-full">{t('organizations.loading_organizations')}</div>;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('organizations.title')}
        subtitle={t('organizations.subtitle')}
        icon={<Building2 className="w-8 h-8" />}
      />

      <StickyToolbar
        actions={
          <button 
            onClick={() => handleOpenModal()}
            className="flex items-center space-x-2 px-6 py-2.5 bg-blue-600 text-white rounded-xl hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none font-bold active:scale-95"
          >
            <Plus className="w-4 h-4" />
            <span>{t('organizations.add_organization')}</span>
          </button>
        }
      />

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {organizations.map((org) => (
          <div 
            key={org.id} 
            className="bg-white dark:bg-dark-surface p-6 rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border group relative hover:shadow-md transition-all cursor-pointer"
            onClick={() => navigate(`/organizations/${org.id}`)}
          >
            <div className="flex justify-between items-start mb-4">
              <div className="w-12 h-12 bg-indigo-50 dark:bg-indigo-900/30 rounded-full flex items-center justify-center">
                <Building className="w-6 h-6 text-indigo-500" />
              </div>
              <div className="flex items-center space-x-1" onClick={(e) => e.stopPropagation()}>
                <button onClick={() => handleOpenModal(org)} className="p-2 text-gray-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-xl transition-all active:scale-95" title={t('common.edit')}>
                  <Edit2 className="w-4 h-4" />
                </button>
                <button onClick={() => handleDelete(org.id, org.name)} className="p-2 text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-xl transition-all active:scale-95" title={t('common.delete')}>
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
            
            <h3 className="text-xl font-bold text-brand-navy dark:text-dark-text mb-1 group-hover:text-blue-600 transition-colors">{org.name}</h3>
            <p className="text-blue-600 dark:text-blue-400 font-medium text-sm mb-4">
              {org.type?.[0]?.coding?.[0]?.display || t('organizations.hospital')}
            </p>
            
            <div className="space-y-3 pt-4 border-t border-gray-50 dark:border-dark-border">
              {org.address?.[0] && (
                <div className="flex items-start text-sm text-gray-500 dark:text-dark-muted">
                  <MapPin className="w-4 h-4 mr-2 mt-0.5 shrink-0" />
                  <span className="truncate">
                    {org.address[0].line?.[0]}, {org.address[0].city}
                  </span>
                </div>
              )}
              {org.telecom?.find(t => t.system === 'email') && (
                <div className="flex items-center text-sm text-gray-500 dark:text-dark-muted">
                  <Mail className="w-4 h-4 mr-2 shrink-0" />
                  <span className="truncate">{org.telecom.find(t => t.system === 'email')?.value}</span>
                </div>
              )}
              {org.telecom?.find(t => t.system === 'phone') && (
                <div className="flex items-center text-sm text-gray-500 dark:text-dark-muted">
                  <Phone className="w-4 h-4 mr-2 shrink-0" />
                  <span>{org.telecom.find(t => t.system === 'phone')?.value}</span>
                </div>
              )}
            </div>

            <div className="mt-6 pt-4 border-t border-gray-50 dark:border-dark-border flex items-center justify-between opacity-0 group-hover:opacity-100 transition-opacity">
               <span className="text-[10px] font-black uppercase tracking-widest text-blue-600 dark:text-blue-400">View Details</span>
               <div className="w-6 h-6 rounded-full bg-blue-50 dark:bg-blue-900/30 flex items-center justify-center">
                  <Plus className="w-3 h-3 text-blue-600 transform rotate-45" />
               </div>
            </div>
          </div>
        ))}
      </div>

      {organizations.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 bg-gray-50 dark:bg-dark-bg/30 rounded-3xl border-2 border-dashed border-gray-200 dark:border-dark-border">
          <div className="w-16 h-16 bg-white dark:bg-dark-surface rounded-full flex items-center justify-center shadow-sm mb-4">
            <Building2 className="w-8 h-8 text-gray-400" />
          </div>
          <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">{t('organizations.no_organizations_added')}</h3>
          <p className="text-gray-500 mt-1 mb-6">{t('organizations.start_adding_subtitle')}</p>
          <button 
            onClick={() => handleOpenModal()} 
            className="px-8 py-2.5 bg-blue-600 text-white rounded-xl font-bold hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none active:scale-95"
          >
            {t('organizations.add_first_organization')}
          </button>
        </div>
      )}

      {/* Modal */}
      <FormModal
        isOpen={isModalOpen}
        onClose={handleCloseModal}
        title={editingOrganization ? t('organizations.edit_organization') : t('organizations.add_new_organization')}
        icon={
          <div className="p-2 bg-blue-50 dark:bg-blue-900/30 rounded-lg">
            <Building2 className="w-5 h-5 text-blue-600" />
          </div>
        }
        onSubmit={handleSubmit}
        submitDisabled={!formData.name.trim()}
        submitLabel={editingOrganization ? t('organizations.update_organization') : t('organizations.save_organization')}
        cancelLabel={t('common.cancel')}
        size="md"
        bodyClassName="p-6 space-y-6"
      >
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="md:col-span-2">
            <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1">{t('organizations.name')} *</label>
            <input
              type="text"
              required
              className="w-full px-4 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-shadow dark:text-dark-text"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              placeholder="e.g. Central City Hospital"
            />
          </div>

          <div>
            <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1">{t('organizations.type')}</label>
            <select
              className="w-full px-4 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-shadow dark:text-dark-text"
              value={formData.type}
              onChange={(e) => setFormData({ ...formData, type: e.target.value })}
            >
              {ORGANIZATION_TYPES.map(ot => (
                <option key={ot.value} value={ot.value}>{t(ot.label)}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1">{t('organizations.email')}</label>
            <input
              type="email"
              className="w-full px-4 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-shadow dark:text-dark-text"
              value={formData.email}
              onChange={(e) => setFormData({ ...formData, email: e.target.value })}
              placeholder="contact@facility.com"
            />
          </div>

          <div>
            <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1">{t('organizations.phone')}</label>
            <input
              type="tel"
              className="w-full px-4 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-shadow dark:text-dark-text"
              value={formData.phone}
              onChange={(e) => setFormData({ ...formData, phone: e.target.value })}
              placeholder="+1 (555) 000-0000"
            />
          </div>
        </div>

        <div className="space-y-4 pt-4 border-t border-gray-50 dark:border-dark-border">
          <h3 className="font-bold text-gray-900 dark:text-dark-text">{t('organizations.address')}</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="md:col-span-2">
              <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1">{t('organizations.street')}</label>
              <input
                type="text"
                className="w-full px-4 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-shadow dark:text-dark-text"
                value={formData.address.line}
                onChange={(e) => setFormData({ ...formData, address: { ...formData.address, line: e.target.value } })}
              />
            </div>
            <div>
              <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1">{t('organizations.city')}</label>
              <input
                type="text"
                className="w-full px-4 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-shadow dark:text-dark-text"
                value={formData.address.city}
                onChange={(e) => setFormData({ ...formData, address: { ...formData.address, city: e.target.value } })}
              />
            </div>
            <div>
              <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1">{t('organizations.state')}</label>
              <input
                type="text"
                className="w-full px-4 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-shadow dark:text-dark-text"
                value={formData.address.state}
                onChange={(e) => setFormData({ ...formData, address: { ...formData.address, state: e.target.value } })}
              />
            </div>
          </div>
        </div>

        <div className="space-y-4 pt-4 border-t border-gray-50 dark:border-dark-border">
          <h3 className="font-bold text-gray-900 dark:text-dark-text">{t('organizations.link_doctors')}</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-40 overflow-y-auto pr-2">
            {doctors.map(doctor => (
              <div
                key={doctor.id}
                onClick={() => toggleDoctorSelection(doctor.id)}
                className={`p-3 rounded-xl border transition-all cursor-pointer flex items-center space-x-3 ${
                  formData.doctor_ids.includes(doctor.id)
                    ? 'bg-blue-50 border-blue-200 dark:bg-blue-900/30 dark:border-blue-800'
                    : 'bg-gray-50 border-gray-200 dark:bg-dark-bg dark:border-dark-border'
                }`}
              >
                <div className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${
                  formData.doctor_ids.includes(doctor.id) ? 'bg-blue-600 border-blue-600' : 'border-gray-300'
                }`}>
                  {formData.doctor_ids.includes(doctor.id) && <Plus className="w-3 h-3 text-white" />}
                </div>
                <div className="truncate">
                  <p className="text-sm font-bold truncate">Dr. {doctor.name}</p>
                  <p className="text-[10px] text-gray-500 truncate">{doctor.specialty}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </FormModal>
    </div>
  );
}

export default OrganizationList;
