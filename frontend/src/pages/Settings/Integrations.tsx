import React, { useState, useEffect } from 'react';
import { integrationService, IntegrationManifest, ActiveIntegration } from '../../services/integrationService';
import { toast } from 'react-toastify';
import { CheckCircle, XCircle, Plus, Settings, RefreshCw } from 'lucide-react';
import ConfigFlowModal from '../../components/integrations/ConfigFlowModal';
import { usePatientStore } from '../../store/slices/patientSlice';

import { Link } from 'react-router-dom';

const Integrations: React.FC = () => {
  const { currentPatient } = usePatientStore();
  const [available, setAvailable] = useState<IntegrationManifest[]>([]);
  const [active, setActive] = useState<ActiveIntegration[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedDomain, setSelectedDomain] = useState<string | null>(null);

  const loadData = async () => {
    if (!currentPatient) return;
    try {
      setLoading(true);
      const [availData, activeData] = await Promise.all([
        integrationService.getAvailable(),
        integrationService.getActive(currentPatient.id)
      ]);
      setAvailable(availData);
      setActive(activeData);
    } catch (error) {
      console.error("Failed to load integrations", error);
      toast.error("Failed to load integrations");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [currentPatient]);

  const handleRemove = async (domain: string) => {
    if (!currentPatient) return;
    if (!window.confirm(`Are you sure you want to remove the ${domain} integration?`)) return;
    
    try {
      await integrationService.removeIntegration(domain, currentPatient.id);
      toast.success("Integration removed");
      loadData();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || "Failed to remove integration");
    }
  };

  const handleSync = async (domain: string) => {
    if (!currentPatient) return;
    try {
      toast.info(`Syncing ${domain}...`);
      const res = await integrationService.syncIntegration(domain, currentPatient.id);
      toast.success(res.message || "Sync completed successfully");
      loadData();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || "Failed to sync integration");
    }
  };

  if (!currentPatient) return <div className="p-8 text-center text-gray-500">Please select a patient to manage integrations.</div>;
  if (loading) return <div className="p-8 text-center text-gray-500">Loading Integrations...</div>;

  return (
    <div className="max-w-4xl mx-auto py-8 px-4">
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Connected Integrations</h1>
      
      {/* Active Integrations */}
      <div className="bg-white shadow rounded-lg mb-8 overflow-hidden">
        <div className="px-4 py-5 sm:px-6 border-b border-gray-200">
          <h3 className="text-lg leading-6 font-medium text-gray-900">Your Integrations</h3>
          <p className="mt-1 text-sm text-gray-500">Manage your connected health data sources.</p>
        </div>
        <ul className="divide-y divide-gray-200">
          {active.length === 0 ? (
            <li className="px-4 py-8 text-center text-gray-500">No active integrations found.</li>
          ) : (
            active.map((integration) => {
              const manifest = available.find(a => a.domain === integration.domain);
              return (
                <li key={integration.id} className="px-4 py-4 sm:px-6 hover:bg-gray-50 flex items-center justify-between">
                  <div className="flex items-center">
                    {integration.status === 'ACTIVE' || integration.status === 'active' ? (
                      <CheckCircle className="h-6 w-6 text-green-500 mr-3" />
                    ) : integration.status === 'ERROR' || integration.status === 'error' ? (
                      <XCircle className="h-6 w-6 text-red-500 mr-3" />
                    ) : (
                      <XCircle className="h-6 w-6 text-gray-500 mr-3" />
                    )}
                    <div>
                      <Link to={`/settings/integrations/${integration.domain}`} className="text-sm font-medium text-blue-600 hover:text-blue-800 hover:underline">
                        {manifest?.name || integration.domain}
                      </Link>
                      <p className="text-sm text-gray-500">
                        {integration.status === 'ERROR' || integration.status === 'error' ? (
                          <span className="text-red-500 font-medium">Integration Error - Requires Attention</span>
                        ) : (
                          <>Last Synced: {integration.last_synced_at ? new Date(integration.last_synced_at).toLocaleString() : 'Never'}</>
                        )}
                      </p>
                    </div>
                  </div>
                  <div className="flex space-x-2">
                    <Link
                      to={`/settings/integrations/${integration.domain}`}
                      className="inline-flex items-center px-3 py-1.5 border border-gray-300 text-xs font-medium rounded shadow-sm text-gray-700 bg-white hover:bg-gray-50 focus:outline-none"
                    >
                      <Settings className="h-4 w-4 mr-1"/> Details
                    </Link>
                    <button 
                      onClick={() => setSelectedDomain(integration.domain)}
                      className="inline-flex items-center px-3 py-1.5 border border-gray-300 shadow-sm text-xs font-medium rounded text-gray-700 bg-white hover:bg-gray-50 focus:outline-none"
                    >
                      <Settings className="h-4 w-4 mr-1"/> Configure
                    </button>
                    <button 
                      onClick={() => handleRemove(integration.domain)}
                      className="inline-flex items-center px-3 py-1.5 border border-transparent text-xs font-medium rounded text-red-700 bg-red-100 hover:bg-red-200 focus:outline-none"
                    >
                      Remove
                    </button>
                  </div>
                </li>
              );
            })
          )}
        </ul>
      </div>

      {/* Available Integrations */}
      <h2 className="text-xl font-bold text-gray-900 mb-4">Available to Connect</h2>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {available.filter(a => !active.some(act => act.domain === a.domain)).map((integration) => (
          <div key={integration.domain} className="relative rounded-lg border border-gray-300 bg-white px-6 py-5 shadow-sm flex items-center space-x-3 hover:border-gray-400 focus-within:ring-2 focus-within:ring-offset-2 focus-within:ring-indigo-500">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-gray-900">{integration.name}</p>
              <p className="text-sm text-gray-500 truncate">Version {integration.version}</p>
            </div>
            <button 
              onClick={(e) => {
                e.stopPropagation();
                setSelectedDomain(integration.domain);
              }}
              className="inline-flex items-center p-2 border border-transparent rounded-full shadow-sm text-white bg-blue-600 hover:bg-blue-700 focus:outline-none z-10 cursor-pointer"
            >
              <Plus className="h-5 w-5" aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>

      {selectedDomain && (
        <ConfigFlowModal 
          domain={selectedDomain} 
          onClose={() => setSelectedDomain(null)} 
          onSuccess={() => {
            setSelectedDomain(null);
            loadData();
          }} 
        />
      )}
    </div>
  );
};

export default Integrations;
