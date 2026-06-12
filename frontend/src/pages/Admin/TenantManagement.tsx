import { useState, useEffect } from 'react';
import { 
  Plus, 
  Settings, 
  Trash2, 
  X, 
  Save, 
  Globe,
  Database,
  Building2,
  Calendar,
  ChevronRight
} from 'lucide-react';
import { listTenants, createTenant, updateTenant, deleteTenant, Tenant } from '../../services/tenantService';
import { useUIStore } from '../../store/slices/uiSlice';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';

function TenantManagement() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingTenant, setEditingOrganization] = useState<Tenant | null>(null);
  const showConfirmation = useUIStore(state => state.showConfirmation);
  
  const [formData, setFormData] = useState({
    name: '',
    settings: '{}'
  });

  const fetchTenants = async () => {
    try {
      const data = await listTenants();
      setTenants(data);
    } catch (err) {
      console.error('Failed to fetch tenants:', err);
    }
  };

  useEffect(() => {
    const loadData = async () => {
      setIsLoading(true);
      await fetchTenants();
      setIsLoading(false);
    };
    loadData();
  }, []);

  const handleOpenModal = (tenant: Tenant | null = null) => {
    if (tenant) {
      setEditingOrganization(tenant);
      setFormData({
        name: tenant.name,
        settings: JSON.stringify(tenant.settings, null, 2)
      });
    } else {
      setEditingOrganization(null);
      setFormData({ name: '', settings: '{}' });
    }
    setIsModalOpen(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const settingsObj = JSON.parse(formData.settings);
      if (editingTenant) {
        await updateTenant(editingTenant.id, formData.name, settingsObj);
      } else {
        await createTenant(formData.name, settingsObj);
      }
      fetchTenants();
      setIsModalOpen(false);
    } catch (err) {
      console.error('Failed to save tenant:', err);
      alert('Invalid settings JSON');
    }
  };

  const handleDelete = (id: string, name: string) => {
    showConfirmation({
      title: 'Delete Installation?',
      message: `Are you sure you want to permanently delete the tenant "${name}"? This will delete all clinical data, documents, and users associated with this installation.`,
      confirmLabel: 'Destroy Everything',
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deleteTenant(id);
          fetchTenants();
        } catch (err) {
          console.error('Failed to delete tenant:', err);
        }
      }
    });
  };

  if (isLoading) {
    return <div className="flex items-center justify-center h-full text-gray-500">Loading system installations...</div>;
  }

  return (
    <div className="space-y-6 pb-20">
      <PageHeader
        title="System Installations"
        subtitle="Global tenant and subscription management"
        icon={<Globe className="w-8 h-8" />}
      />

      <StickyToolbar
        actions={
          <button 
            onClick={() => handleOpenModal()}
            className="flex items-center space-x-2 px-6 py-2.5 bg-indigo-600 text-white rounded-xl hover:bg-indigo-700 transition-all shadow-lg shadow-indigo-200/50 dark:shadow-none font-bold active:scale-95"
          >
            <Plus className="w-4 h-4" />
            <span>New Installation</span>
          </button>
        }
      />

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        {tenants.map((tenant) => (
          <div 
            key={tenant.id} 
            className="bg-white dark:bg-dark-surface p-8 rounded-[2rem] shadow-sm border border-gray-100 dark:border-dark-border group relative hover:shadow-xl transition-all duration-300"
          >
            <div className="flex justify-between items-start mb-6">
              <div className="flex items-center space-x-4">
                <div className="w-14 h-14 bg-indigo-50 dark:bg-indigo-900/30 rounded-2xl flex items-center justify-center border border-indigo-100 dark:border-indigo-800">
                  <Database className="w-7 h-7 text-indigo-500" />
                </div>
                <div>
                  <h3 className="text-xl font-black text-[#1a2b4b] dark:text-dark-text tracking-tight">{tenant.name}</h3>
                  <div className="flex items-center space-x-2 mt-1">
                    <span className="px-2 py-0.5 rounded-md bg-green-50 dark:bg-green-900/20 text-green-600 text-[10px] font-black uppercase tracking-widest border border-green-100 dark:border-green-800">Active</span>
                    <span className="text-[10px] text-gray-400 font-mono">{tenant.id}</span>
                  </div>
                </div>
              </div>
              <div className="flex items-center space-x-1 opacity-0 group-hover:opacity-100 transition-opacity">
                <button onClick={() => handleOpenModal(tenant)} className="p-2.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 rounded-xl transition-all" title="Configure">
                  <Settings className="w-5 h-5" />
                </button>
                <button onClick={() => handleDelete(tenant.id, tenant.name)} className="p-2.5 text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-xl transition-all" title="Terminate">
                  <Trash2 className="w-5 h-5" />
                </button>
              </div>
            </div>
            
            <div className="grid grid-cols-2 gap-4 py-6 border-y border-gray-50 dark:border-dark-border">
              <div className="space-y-1">
                <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest flex items-center">
                   <Building2 className="w-3 h-3 mr-1" /> Managed Units
                </p>
                <p className="text-lg font-bold text-[#1a2b4b] dark:text-dark-text">--</p>
              </div>
              <div className="space-y-1">
                <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest flex items-center">
                   <Calendar className="w-3 h-3 mr-1" /> Created On
                </p>
                <p className="text-lg font-bold text-[#1a2b4b] dark:text-dark-text">
                  {tenant.created_at ? new Date(tenant.created_at).toLocaleDateString() : 'Unknown'}
                </p>
              </div>
            </div>

            <div className="mt-6 flex items-center justify-between group/link cursor-pointer">
              <span className="text-xs font-black uppercase tracking-tighter text-indigo-600 dark:text-indigo-400 group-hover/link:underline">Browse clinical data</span>
              <div className="w-8 h-8 rounded-full bg-indigo-50 dark:bg-indigo-900/30 flex items-center justify-center text-indigo-600">
                <ChevronRight className="w-4 h-4 group-hover/link:translate-x-1 transition-transform" />
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Modal */}
      {isModalOpen && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-[1000] p-4 animate-in fade-in duration-200">
          <div className="bg-white dark:bg-dark-surface rounded-3xl w-full max-w-2xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200">
            <div className="px-8 py-6 border-b border-gray-100 dark:border-dark-border flex justify-between items-center bg-gray-50/50 dark:bg-dark-bg/50">
              <h2 className="text-xl font-black text-[#1a2b4b] dark:text-dark-text uppercase tracking-tight">
                {editingTenant ? 'Configure Installation' : 'Provision New Tenant'}
              </h2>
              <button onClick={() => setIsModalOpen(false)} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-border rounded-full transition-colors text-gray-400">
                <X className="w-5 h-5" />
              </button>
            </div>
            
            <form onSubmit={handleSubmit} className="p-8 space-y-6">
              <div className="space-y-4">
                <div>
                  <label className="block text-xs font-black text-gray-400 uppercase tracking-widest mb-2">Display Name</label>
                  <input
                    type="text"
                    required
                    className="w-full px-5 py-3.5 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-2xl focus:ring-4 focus:ring-indigo-500/10 outline-none transition-all dark:text-dark-text font-bold text-lg"
                    value={formData.name}
                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    placeholder="e.g. Northwell Health System"
                  />
                </div>
                
                <div>
                  <label className="block text-xs font-black text-gray-400 uppercase tracking-widest mb-2">Configuration (JSON)</label>
                  <textarea
                    rows={8}
                    className="w-full px-5 py-4 bg-gray-900 text-green-400 border border-gray-800 rounded-2xl focus:ring-4 focus:ring-indigo-500/10 outline-none transition-all font-mono text-sm leading-relaxed"
                    value={formData.settings}
                    onChange={(e) => setFormData({ ...formData, settings: e.target.value })}
                    spellCheck={false}
                  />
                  <p className="mt-2 text-[10px] text-gray-400 italic">Inject tenant-level feature flags and AI overrides here.</p>
                </div>
              </div>

              <div className="pt-4 flex space-x-4">
                <button
                  type="button"
                  onClick={() => setIsModalOpen(false)}
                  className="flex-1 px-6 py-4 border border-gray-100 dark:border-dark-border rounded-2xl hover:bg-gray-50 dark:hover:bg-dark-border transition-all font-bold text-gray-500 uppercase tracking-widest text-xs"
                >
                  Discard
                </button>
                <button
                  type="submit"
                  className="flex-[2] px-6 py-4 bg-indigo-600 text-white rounded-2xl hover:bg-indigo-700 transition-all font-bold flex items-center justify-center space-x-2 shadow-xl shadow-indigo-200 dark:shadow-none active:scale-95"
                >
                  <Save className="w-5 h-5" />
                  <span className="uppercase tracking-widest text-sm">Deploy Configuration</span>
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

export default TenantManagement;
