import React, { useState, useEffect } from 'react';
import { integrationService, IntegrationManifest, ActiveIntegration } from '../../services/integrationService';
import { toast } from 'react-toastify';
import { CheckCircle, XCircle, Plus, Settings, RefreshCw, FileText, Server, Cloud, Globe } from 'lucide-react';
import ConfigFlowModal from '../../components/integrations/ConfigFlowModal';
import IntegrationDocsModal from '../../components/integrations/IntegrationDocsModal';
import { usePatientStore } from '../../store/slices/patientSlice';

import { Link } from 'react-router-dom';

const Integrations: React.FC = () => {
  const { currentPatient } = usePatientStore();
  const [available, setAvailable] = useState<IntegrationManifest[]>([]);
  const [active, setActive] = useState<ActiveIntegration[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedDomain, setSelectedDomain] = useState<string | null>(null);
  const [docsDomain, setDocsDomain] = useState<string | null>(null);
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);

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

  const handleRemove = async (integrationId: string, domain: string) => {
    if (!currentPatient) return;
    if (!window.confirm(`Are you sure you want to remove this ${domain} integration instance?`)) return;
    
    try {
      await integrationService.removeIntegration(integrationId, currentPatient.id);
      toast.success("Integration removed");
      loadData();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || "Failed to remove integration");
    }
  };

  // Get unique categories from available integrations
  const categories = Array.from(new Set(available.flatMap(i => i.categories || ['Uncategorized']))).sort();

  const filteredAvailable = available.filter(integration => {
    if (!selectedCategory) return true;
    const itemCategories = integration.categories || ['Uncategorized'];
    return itemCategories.includes(selectedCategory);
  });

  const getAccessIcon = (type?: string) => {
    switch(type) {
      case 'Local': return <Server className="h-4 w-4 text-gray-500" />;
      case 'Cloud': return <Cloud className="h-4 w-4 text-blue-500" />;
      case 'Local & Cloud': return <Globe className="h-4 w-4 text-purple-500" />;
      default: return null;
    }
  };

  if (!currentPatient) return <div className="p-8 text-center text-gray-500">Please select a patient to manage integrations.</div>;
  if (loading) return <div className="p-8 text-center text-gray-500">Loading Integrations...</div>;

  return (
    <div className="max-w-5xl mx-auto py-8 px-4">
      <h1 className="text-2xl font-bold text-gray-900 dark:text-white mb-6">Connected Integrations</h1>
      
      {/* Active Integrations */}
      <div className="bg-white dark:bg-dark-surface shadow rounded-lg mb-8 overflow-hidden border border-gray-200 dark:border-dark-border">
        <div className="px-4 py-5 sm:px-6 border-b border-gray-200 dark:border-dark-border">
          <h3 className="text-lg leading-6 font-medium text-gray-900 dark:text-white">Your Integrations</h3>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Manage your connected health data sources.</p>
        </div>
        <ul className="divide-y divide-gray-200 dark:divide-dark-border">
          {active.length === 0 ? (
            <li className="px-4 py-8 text-center text-gray-500 dark:text-gray-400">No active integrations found.</li>
          ) : (
            active.map((integration) => {
              const manifest = available.find(a => a.domain === integration.domain);
              return (
                <li key={integration.id} className="px-4 py-4 sm:px-6 hover:bg-gray-50 dark:hover:bg-dark-border/50 flex items-center justify-between">
                  <div className="flex items-center">
                    {integration.status === 'ACTIVE' || integration.status === 'active' ? (
                      <CheckCircle className="h-6 w-6 text-green-500 mr-3" />
                    ) : integration.status === 'ERROR' || integration.status === 'error' ? (
                      <XCircle className="h-6 w-6 text-red-500 mr-3" />
                    ) : (
                      <XCircle className="h-6 w-6 text-gray-500 mr-3" />
                    )}
                    <div>
                      <div className="flex items-center gap-2">
                        <Link to={`/settings/integrations/${integration.id}`} className="text-sm font-medium text-blue-600 dark:text-blue-400 hover:text-blue-800 hover:underline">
                          {integration.instance_name || manifest?.name || integration.domain}
                        </Link>
                        {manifest?.access_type && getAccessIcon(manifest.access_type)}
                      </div>
                      <p className="text-sm text-gray-500 dark:text-gray-400">
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
                      to={`/settings/integrations/${integration.id}`}
                      className="inline-flex items-center px-3 py-1.5 border border-gray-300 dark:border-gray-600 text-xs font-medium rounded shadow-sm text-gray-700 dark:text-gray-200 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700 focus:outline-none"
                    >
                      <Settings className="h-4 w-4 mr-1"/> Details
                    </Link>
                    <button 
                      onClick={() => handleRemove(integration.id, integration.domain)}
                      className="inline-flex items-center px-3 py-1.5 border border-transparent text-xs font-medium rounded text-red-700 bg-red-100 dark:bg-red-900/30 dark:text-red-400 hover:bg-red-200 dark:hover:bg-red-900/50 focus:outline-none"
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
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-4 gap-4">
        <h2 className="text-xl font-bold text-gray-900 dark:text-white">Available to Connect</h2>
        
        {/* Category Filter */}
        {categories.length > 0 && (
          <div className="flex overflow-x-auto pb-2 sm:pb-0 gap-2 hide-scrollbar">
            <button
              onClick={() => setSelectedCategory(null)}
              className={`px-3 py-1 text-sm rounded-full whitespace-nowrap transition-colors ${
                selectedCategory === null 
                  ? 'bg-gray-900 text-white dark:bg-white dark:text-gray-900' 
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700'
              }`}
            >
              All
            </button>
            {categories.map(category => (
              <button
                key={category}
                onClick={() => setSelectedCategory(category)}
                className={`px-3 py-1 text-sm rounded-full whitespace-nowrap transition-colors ${
                  selectedCategory === category
                    ? 'bg-blue-600 text-white dark:bg-blue-500' 
                    : 'bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700'
                }`}
              >
                {category}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {filteredAvailable.map((integration) => (
          <div key={integration.domain} className="relative rounded-xl border border-gray-200 dark:border-dark-border bg-white dark:bg-dark-surface p-5 shadow-sm flex flex-col justify-between hover:shadow-md transition-shadow">
            
            <div className="flex items-start justify-between mb-2">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <h3 className="text-base font-bold text-gray-900 dark:text-white leading-tight">{integration.name}</h3>
                  {getAccessIcon(integration.access_type)}
                </div>
                <span className="inline-block px-2 py-0.5 text-[10px] font-semibold tracking-wide text-gray-500 bg-gray-100 dark:bg-gray-800 dark:text-gray-400 rounded">
                  {integration.author === 'Core' ? 'OFFICIAL' : 'COMMUNITY'}
                </span>
              </div>
            </div>

            <p className="text-sm text-gray-600 dark:text-gray-300 mt-2 mb-4 line-clamp-2" title={integration.description}>
              {integration.description || 'No description provided.'}
            </p>

            <div className="flex items-center justify-between pt-4 border-t border-gray-100 dark:border-dark-border mt-auto">
              <div className="text-xs text-gray-400 dark:text-gray-500">v{integration.version}</div>
              <div className="flex space-x-2 z-10">
                <button 
                  onClick={(e) => {
                    e.stopPropagation();
                    setDocsDomain(integration.domain);
                  }}
                  className="inline-flex items-center px-3 py-1.5 border border-gray-300 dark:border-gray-600 rounded shadow-sm text-xs font-medium text-gray-700 dark:text-gray-200 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700 focus:outline-none cursor-pointer"
                  title="Documentation"
                >
                  <FileText className="h-4 w-4 mr-1" aria-hidden="true" />
                  Docs
                </button>
                <button 
                  onClick={(e) => {
                    e.stopPropagation();
                    setSelectedDomain(integration.domain);
                  }}
                  className="inline-flex items-center px-3 py-1.5 border border-transparent rounded shadow-sm text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 focus:outline-none cursor-pointer"
                  title="Connect"
                >
                  <Plus className="h-4 w-4 mr-1" aria-hidden="true" />
                  Add
                </button>
              </div>
            </div>
          </div>
        ))}
        {filteredAvailable.length === 0 && (
          <div className="col-span-full py-12 text-center text-gray-500 dark:text-gray-400">
            No integrations found in this category.
          </div>
        )}
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

      {docsDomain && (
        <IntegrationDocsModal
          domain={docsDomain}
          onClose={() => setDocsDomain(null)}
        />
      )}
    </div>
  );
};

export default Integrations;
