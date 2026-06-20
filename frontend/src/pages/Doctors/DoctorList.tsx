import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useLocation } from 'react-router-dom';
import { Plus, User, Mail, Phone, ShieldCheck, Trash2, Edit2, X, Save, Stethoscope, Building, Globe, Hash, Info, PlusCircle } from 'lucide-react';
import { listDoctors, createDoctor, updateDoctor, deleteDoctor, Doctor, ContactPoint } from '../../services/doctorService';
import { useUIStore } from '../../store/slices/uiSlice';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';

function DoctorList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const [doctors, setDoctors] = useState<Doctor[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingDoctor, setEditingDoctor] = useState<Doctor | null>(null);
  const showConfirmation = useUIStore(state => state.showConfirmation);
  
  // Form state
  const [formData, setFormData] = useState<{
    name: string;
    specialty: string;
    license_number: string;
    email: string;
    phone: string;
    office_number: string;
    office_details: string;
    address: {
      line: string[];
      city: string;
      state: string;
      postalCode: string;
      country: string;
    };
    telecom: ContactPoint[];
  }>({
    name: '',
    specialty: '',
    license_number: '',
    email: '',
    phone: '',
    office_number: '',
    office_details: '',
    address: {
      line: [''],
      city: '',
      state: '',
      postalCode: '',
      country: ''
    },
    telecom: []
  });

  const fetchDoctors = async () => {
    try {
      const data = await listDoctors();
      setDoctors(data);
    } catch (err) {
      console.error('Failed to fetch doctors:', err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchDoctors();
  }, []);

  // Handle editing doctor passed from detail page
  useEffect(() => {
    const state = location.state as { editingDoctor?: Doctor };
    if (state?.editingDoctor) {
      handleOpenModal(state.editingDoctor);
      // Clear state so it doesn't reopen on refresh
      navigate(location.pathname, { replace: true, state: {} });
    }
  }, [location.state]);

  const handleOpenModal = (doctor: Doctor | null = null) => {
    if (doctor) {
      const addr = doctor.address?.[0];
      setEditingDoctor(doctor);
      setFormData({
        name: doctor.name,
        specialty: doctor.specialty || '',
        license_number: doctor.license_number || '',
        email: doctor.email || '',
        phone: doctor.phone || '',
        office_number: doctor.office_number || '',
        office_details: doctor.office_details || '',
        address: {
          line: addr?.line || [''],
          city: addr?.city || '',
          state: addr?.state || '',
          postalCode: addr?.postalCode || '',
          country: addr?.country || ''
        },
        telecom: doctor.telecom || []
      });
    } else {
      setEditingDoctor(null);
      setFormData({ 
        name: '', 
        specialty: '', 
        license_number: '', 
        email: '', 
        phone: '',
        office_number: '',
        office_details: '',
        address: {
          line: [''],
          city: '',
          state: '',
          postalCode: '',
          country: ''
        },
        telecom: []
      });
    }
    setIsModalOpen(true);
  };

  const handleAddTelecom = () => {
    setFormData({
      ...formData,
      telecom: [...formData.telecom, { system: 'phone', value: '', use: 'work' }]
    });
  };

  const handleRemoveTelecom = (index: number) => {
    setFormData({
      ...formData,
      telecom: formData.telecom.filter((_, i) => i !== index)
    });
  };

  const handleUpdateTelecom = (index: number, field: keyof ContactPoint, value: string) => {
    const updated = [...formData.telecom];
    updated[index] = { ...updated[index], [field]: value };
    setFormData({ ...formData, telecom: updated });
  };

  const handleCloseModal = () => {
    setIsModalOpen(false);
    setEditingDoctor(null);
  };

  const handleUpdateAddress = (field: keyof typeof formData.address, value: string) => {
    if (field === 'line') {
      setFormData({ ...formData, address: { ...formData.address, line: [value] } });
    } else {
      setFormData({ ...formData, address: { ...formData.address, [field]: value } });
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      // Practitioner.address is FHIR 0..* — send the single edited address as a list.
      const payload = { ...formData, address: [formData.address] };
      if (editingDoctor) {
        await updateDoctor(editingDoctor.id, payload);
      } else {
        await createDoctor(payload);
      }
      fetchDoctors();
      handleCloseModal();
    } catch (err) {
      console.error('Failed to save doctor:', err);
    }
  };

  const handleDelete = (id: string, name: string) => {
    showConfirmation({
      title: t('doctors.delete_doctor_title'),
      message: t('doctors.delete_doctor_confirm', { name }),
      confirmLabel: t('common.delete'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deleteDoctor(id);
          fetchDoctors();
        } catch (err) {
          console.error('Failed to delete doctor:', err);
          alert(t('doctors.failed_delete'));
        }
      }
    });
  };

  if (isLoading) {
    return <div className="flex items-center justify-center h-full">{t('doctors.loading_doctors')}</div>;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('doctors.management_title')}
        subtitle={t('doctors.management_subtitle')}
        icon={<Stethoscope className="w-8 h-8" />}
      />

      <StickyToolbar
        actions={
          <button 
            onClick={() => handleOpenModal()}
            className="flex items-center space-x-2 px-6 py-2.5 bg-blue-600 text-white rounded-xl hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none font-bold active:scale-95"
          >
            <Plus className="w-4 h-4" />
            <span>{t('doctors.add_doctor')}</span>
          </button>
        }
      />

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {doctors.map((doctor) => (
          <div 
            key={doctor.id} 
            className="bg-white dark:bg-dark-surface p-6 rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border group relative hover:shadow-md transition-all cursor-pointer"
            onClick={() => navigate(`/doctors/${doctor.id}`)}
          >
            <div className="flex justify-between items-start mb-4">
              <div className="w-12 h-12 bg-blue-50 dark:bg-blue-900/30 rounded-full flex items-center justify-center">
                <User className="w-6 h-6 text-blue-500" />
              </div>
              <div className="flex items-center space-x-1" onClick={(e) => e.stopPropagation()}>
                <button onClick={() => handleOpenModal(doctor)} className="p-2 text-gray-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-xl transition-all active:scale-95" title={t('doctors.edit_doctor')}>
                  <Edit2 className="w-4 h-4" />
                </button>
                <button onClick={() => handleDelete(doctor.id, doctor.name)} className="p-2 text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-xl transition-all active:scale-95" title={t('doctors.delete_doctor_title')}>
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
            
            <h3 className="text-xl font-bold text-[#1a2b4b] dark:text-dark-text mb-1 group-hover:text-blue-600 transition-colors">{t('doctors.dr')} {doctor.name}</h3>
            <p className="text-blue-600 dark:text-blue-400 font-medium text-sm mb-4">{doctor.specialty || t('doctors.general_practitioner')}</p>
            
            <div className="space-y-3 pt-4 border-t border-gray-50 dark:border-dark-border">
              {doctor.license_number && (
                <div className="flex items-center text-sm text-gray-500 dark:text-dark-muted">
                  <ShieldCheck className="w-4 h-4 mr-2 text-green-500" />
                  <span>{t('doctors.license')}: {doctor.license_number}</span>
                </div>
              )}
              {doctor.email && (
                <div className="flex items-center text-sm text-gray-500 dark:text-dark-muted">
                  <Mail className="w-4 h-4 mr-2" />
                  <span className="truncate">{doctor.email}</span>
                </div>
              )}
              {doctor.phone && (
                <div className="flex items-center text-sm text-gray-500 dark:text-dark-muted">
                  <Phone className="w-4 h-4 mr-2" />
                  <span>{doctor.phone}</span>
                </div>
              )}
              {doctor.office_number && (
                <div className="flex items-center text-sm text-gray-500 dark:text-dark-muted">
                  <Building className="w-4 h-4 mr-2" />
                  <span>{doctor.office_number}</span>
                </div>
              )}
            </div>

            <div className="mt-6 pt-4 border-t border-gray-50 dark:border-dark-border flex items-center justify-between opacity-0 group-hover:opacity-100 transition-opacity">
               <span className="text-[10px] font-black uppercase tracking-widest text-blue-600 dark:text-blue-400">View Full Profile</span>
               <div className="w-6 h-6 rounded-full bg-blue-50 dark:bg-blue-900/30 flex items-center justify-center">
                  <Plus className="w-3 h-3 text-blue-600 transform rotate-45" />
               </div>
            </div>
          </div>
        ))}
      </div>

      {doctors.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 bg-gray-50 dark:bg-dark-bg/30 rounded-3xl border-2 border-dashed border-gray-200 dark:border-dark-border">
          <div className="w-16 h-16 bg-white dark:bg-dark-surface rounded-full flex items-center justify-center shadow-sm mb-4">
            <User className="w-8 h-8 text-gray-400" />
          </div>
          <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">{t('doctors.no_doctors_added')}</h3>
          <p className="text-gray-500 mt-1 mb-6">{t('doctors.start_adding_subtitle')}</p>
          <button 
            onClick={() => handleOpenModal()} 
            className="px-8 py-2.5 bg-blue-600 text-white rounded-xl font-bold hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none active:scale-95"
          >
            {t('doctors.add_first_doctor')}
          </button>
        </div>
      )}

      {/* Modal */}
      {isModalOpen && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[1000] p-4 overflow-y-auto">
          <div className="bg-white dark:bg-dark-surface rounded-2xl w-full max-w-2xl shadow-2xl overflow-hidden my-auto animate-in fade-in zoom-in duration-200">
            <div className="px-6 py-4 border-b border-gray-100 dark:border-dark-border flex justify-between items-center bg-gray-50/50 dark:bg-dark-surface/50">
              <div className="flex items-center space-x-3">
                <div className="w-10 h-10 bg-blue-100 dark:bg-blue-900/30 rounded-full flex items-center justify-center">
                  <User className="w-5 h-5 text-blue-600" />
                </div>
                <h2 className="text-xl font-bold">{editingDoctor ? t('doctors.edit_doctor') : t('doctors.add_new_doctor')}</h2>
              </div>
              <button onClick={handleCloseModal} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-border rounded-full transition-colors">
                <X className="w-5 h-5" />
              </button>
            </div>
            
            <form onSubmit={handleSubmit} className="p-6 space-y-8 max-h-[80vh] overflow-y-auto custom-scrollbar">
              {/* Basic Info */}
              <div className="space-y-4">
                <div className="flex items-center space-x-2 text-blue-600 dark:text-blue-400 font-bold text-sm uppercase tracking-wider">
                   <Info className="w-4 h-4" />
                   <span>{t('patients.personal_info')}</span>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1">{t('doctors.full_name')} *</label>
                    <input
                      type="text"
                      required
                      className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-shadow dark:text-dark-text"
                      value={formData.name}
                      onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                      placeholder="e.g. Sarah Wilson"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1">{t('doctors.specialty')}</label>
                    <input
                      type="text"
                      className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-shadow dark:text-dark-text"
                      value={formData.specialty}
                      onChange={(e) => setFormData({ ...formData, specialty: e.target.value })}
                      placeholder="e.g. Cardiology"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1">{t('doctors.license_number')}</label>
                    <div className="relative">
                      <ShieldCheck className="absolute left-3 top-3 w-4 h-4 text-gray-400" />
                      <input
                        type="text"
                        className="w-full pl-10 pr-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-shadow dark:text-dark-text"
                        value={formData.license_number}
                        onChange={(e) => setFormData({ ...formData, license_number: e.target.value })}
                        placeholder="Medical License ID"
                      />
                    </div>
                  </div>
                  <div>
                    <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1">{t('doctors.email')} (Primary)</label>
                    <div className="relative">
                      <Mail className="absolute left-3 top-3 w-4 h-4 text-gray-400" />
                      <input
                        type="email"
                        className="w-full pl-10 pr-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-shadow dark:text-dark-text"
                        value={formData.email}
                        onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                        placeholder="doctor@example.com"
                      />
                    </div>
                  </div>
                </div>
              </div>

              {/* Office Details */}
              <div className="space-y-4 pt-6 border-t border-gray-100 dark:border-dark-border">
                <div className="flex items-center space-x-2 text-blue-600 dark:text-blue-400 font-bold text-sm uppercase tracking-wider">
                   <Building className="w-4 h-4" />
                   <span>Office Details</span>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <div className="md:col-span-1">
                    <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1">{t('doctors.office_number')}</label>
                    <div className="relative">
                      <Hash className="absolute left-3 top-3 w-4 h-4 text-gray-400" />
                      <input
                        type="text"
                        className="w-full pl-10 pr-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-shadow dark:text-dark-text"
                        value={formData.office_number}
                        onChange={(e) => setFormData({ ...formData, office_number: e.target.value })}
                        placeholder="Room 302"
                      />
                    </div>
                  </div>
                  <div className="md:col-span-2">
                    <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1">{t('doctors.office_details')}</label>
                    <input
                      type="text"
                      className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-shadow dark:text-dark-text"
                      value={formData.office_details}
                      onChange={(e) => setFormData({ ...formData, office_details: e.target.value })}
                      placeholder={t('doctors.office_details_placeholder')}
                    />
                  </div>
                </div>

                <div className="space-y-3">
                  <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted">{t('doctors.address')}</label>
                  <input
                    type="text"
                    className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-shadow dark:text-dark-text mb-2"
                    value={formData.address.line[0] || ''}
                    onChange={(e) => handleUpdateAddress('line', e.target.value)}
                    placeholder={t('doctors.street')}
                  />
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <input
                      type="text"
                      className="px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-shadow dark:text-dark-text text-sm"
                      value={formData.address.city}
                      onChange={(e) => handleUpdateAddress('city', e.target.value)}
                      placeholder={t('doctors.city')}
                    />
                    <input
                      type="text"
                      className="px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-shadow dark:text-dark-text text-sm"
                      value={formData.address.state}
                      onChange={(e) => handleUpdateAddress('state', e.target.value)}
                      placeholder={t('doctors.state')}
                    />
                    <input
                      type="text"
                      className="px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-shadow dark:text-dark-text text-sm"
                      value={formData.address.postalCode}
                      onChange={(e) => handleUpdateAddress('postalCode', e.target.value)}
                      placeholder={t('doctors.postal_code')}
                    />
                    <input
                      type="text"
                      className="px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-shadow dark:text-dark-text text-sm"
                      value={formData.address.country}
                      onChange={(e) => handleUpdateAddress('country', e.target.value)}
                      placeholder={t('doctors.country')}
                    />
                  </div>
                </div>
              </div>

              {/* Additional Contact Methods (Telecom) */}
              <div className="space-y-4 pt-6 border-t border-gray-100 dark:border-dark-border">
                <div className="flex justify-between items-center">
                  <div className="flex items-center space-x-2 text-blue-600 dark:text-blue-400 font-bold text-sm uppercase tracking-wider">
                     <Globe className="w-4 h-4" />
                     <span>{t('doctors.telecom')}</span>
                  </div>
                  <button 
                    type="button" 
                    onClick={handleAddTelecom}
                    className="text-xs font-bold text-blue-600 hover:text-blue-700 flex items-center space-x-1"
                  >
                    <PlusCircle className="w-3 h-3" />
                    <span>{t('doctors.add_phone')}</span>
                  </button>
                </div>

                {formData.telecom.length === 0 && (
                  <p className="text-sm text-gray-500 italic px-4 py-3 bg-gray-50 dark:bg-dark-bg/30 rounded-xl text-center border border-dashed border-gray-200 dark:border-dark-border">
                    No additional contact methods added.
                  </p>
                )}

                <div className="space-y-3">
                  {formData.telecom.map((item, index) => (
                    <div key={index} className="flex items-center space-x-2 animate-in slide-in-from-left-2 duration-200">
                      <select
                        className="px-3 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-sm outline-none focus:ring-2 focus:ring-blue-500"
                        value={item.system}
                        onChange={(e) => handleUpdateTelecom(index, 'system', e.target.value)}
                      >
                        <option value="phone">Phone</option>
                        <option value="email">Email</option>
                        <option value="fax">Fax</option>
                        <option value="sms">SMS</option>
                        <option value="other">Other</option>
                      </select>
                      <select
                        className="px-3 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-sm outline-none focus:ring-2 focus:ring-blue-500"
                        value={item.use}
                        onChange={(e) => handleUpdateTelecom(index, 'use', e.target.value)}
                      >
                        <option value="work">{t('doctors.work')}</option>
                        <option value="mobile">{t('doctors.mobile')}</option>
                        <option value="home">{t('doctors.home')}</option>
                        <option value="other">{t('doctors.other')}</option>
                      </select>
                      <input
                        type="text"
                        required
                        className="flex-1 px-4 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-sm outline-none focus:ring-2 focus:ring-blue-500"
                        value={item.value}
                        onChange={(e) => handleUpdateTelecom(index, 'value', e.target.value)}
                        placeholder="Value..."
                      />
                      <button 
                        type="button"
                        onClick={() => handleRemoveTelecom(index)}
                        className="p-2 text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  ))}
                </div>
              </div>

              <div className="pt-6 border-t border-gray-100 dark:border-dark-border flex space-x-3">
                <button
                  type="button"
                  onClick={handleCloseModal}
                  className="flex-1 px-6 py-3 border border-gray-200 dark:border-dark-border rounded-xl hover:bg-gray-50 dark:hover:bg-dark-border transition-colors font-bold text-gray-700 dark:text-dark-muted"
                >
                  {t('common.cancel')}
                </button>
                <button
                  type="submit"
                  className="flex-1 px-6 py-3 bg-blue-600 text-white rounded-xl hover:bg-blue-700 transition-all font-bold flex items-center justify-center space-x-2 shadow-lg shadow-blue-200/50 dark:shadow-none active:scale-95"
                >
                  <Save className="w-4 h-4" />
                  <span>{editingDoctor ? t('doctors.update_doctor') : t('doctors.save_doctor')}</span>
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

export default DoctorList;
