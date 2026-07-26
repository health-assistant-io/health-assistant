import React, { useState, useEffect, useMemo } from 'react';
import { integrationService, IntegrationManifest, ActiveIntegration } from '../../services/integrationService';
import { toast } from 'react-toastify';
import { CheckCircle, XCircle, Settings, Server, Cloud, Globe, Search, LayoutGrid } from 'lucide-react';
import ConfigFlowModal from '../../components/integrations/ConfigFlowModal';
import IntegrationDocsModal from '../../components/integrations/IntegrationDocsModal';
import BrowseIntegrationsModal from '../../components/integrations/BrowseIntegrationsModal';
import { NoPatientState } from '../../components/ui/NoPatientState';
import { usePatientStore } from '../../store/slices/patientSlice';

import { Link } from 'react-router-dom';

const Integrations: React.FC = () => {
  const { currentPatient } = usePatientStore();
  const [available, setAvailable] = useState<IntegrationManifest[]>([]);
  const [active, setActive] = useState<ActiveIntegration[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedDomain, setSelectedDomain] = useState<string | null>(null);
  const [docsDomain, setDocsDomain] = useState<string | null>(null);
  const [browseOpen, setBrowseOpen] = useState(false);

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

  // Get unique categories from available integrations (for the browse modal badge).
  const categoryCount = Array.from(new Set(available.flatMap(i => i.categories || ['Uncategorized']))).length;
  const connectedDomains = useMemo(() => new Set(active.map((i) => i.domain)), [active]);

  const getAccessIcon = (type?: string) => {
    switch(type) {
      case 'local': return <Server className="h-4 w-4 text-gray-500" />;
      case 'cloud': return <Cloud className="h-4 w-4 text-blue-500" />;
      case 'hybrid': return <Globe className="h-4 w-4 text-purple-500" />;
      default: return null;
    }
  };

  if (!currentPatient) return <NoPatientState icon={Server} contextKey="integrations" />;
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
                      <div className="flex items-center gap-2 mb-1">
                        <Link to={`/settings/integrations/${integration.id}`} className="text-base font-bold text-blue-600 dark:text-blue-400 hover:text-blue-800 hover:underline">
                          {integration.instance_name || manifest?.name || integration.domain}
                        </Link>
                        {integration.instance_name && (
                           <span className="px-2 py-0.5 text-[10px] uppercase font-bold tracking-wider text-gray-500 bg-gray-100 dark:bg-gray-800 dark:text-gray-400 rounded-full whitespace-nowrap">
                             via {manifest?.name || integration.domain}
                           </span>
                        )}
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

      {/* Available Integrations — launcher card */}
      <div className="bg-white dark:bg-dark-surface shadow rounded-lg border border-gray-200 dark:border-dark-border">
        <div className="p-6 sm:p-8 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-start gap-4">
            <div className="w-12 h-12 rounded-2xl bg-blue-50 dark:bg-blue-900/30 flex items-center justify-center shrink-0">
              <LayoutGrid className="w-6 h-6 text-blue-600 dark:text-blue-400" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-gray-900 dark:text-white">Available to Connect</h2>
              <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                {available.length} {available.length === 1 ? 'integration' : 'integrations'} across {categoryCount} {categoryCount === 1 ? 'category' : 'categories'}.
                Browse, search, and filter to find what you need.
              </p>
            </div>
          </div>
          <button
            onClick={() => setBrowseOpen(true)}
            className="inline-flex items-center justify-center gap-2 px-5 py-2.5 bg-blue-600 text-white rounded-xl text-sm font-semibold hover:bg-blue-700 shadow-sm shadow-blue-200/50 transition-colors shrink-0"
          >
            <Search className="w-4 h-4" />
            Browse &amp; Connect
          </button>
        </div>
      </div>

      <BrowseIntegrationsModal
        open={browseOpen}
        available={available}
        connectedDomains={connectedDomains}
        onClose={() => setBrowseOpen(false)}
        onAdd={(domain) => {
          setBrowseOpen(false);
          setSelectedDomain(domain);
        }}
        onDocs={(domain) => {
          setBrowseOpen(false);
          setDocsDomain(domain);
        }}
      />

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
