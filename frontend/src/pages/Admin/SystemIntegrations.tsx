import React, { useState, useEffect } from 'react';
import api from '../../api/axios';
import { toast } from 'react-toastify';
import { PageHeader } from '../../components/ui/PageHeader';
import { Globe, Link as LinkIcon, Power, PowerOff } from 'lucide-react';

interface SystemIntegration {
  domain: string;
  name?: string;
  version?: string;
  is_enabled: boolean;
}

const SystemIntegrations: React.FC = () => {
  const [integrations, setIntegrations] = useState<SystemIntegration[]>([]);
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState<string | null>(null);

  const fetchIntegrations = async () => {
    try {
      setLoading(true);
      const response = await api.get('/admin/integrations');
      setIntegrations(response.data);
    } catch (error) {
      console.error("Failed to load system integrations", error);
      toast.error("Failed to load system integrations");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchIntegrations();
  }, []);

  const handleToggle = async (domain: string, currentlyEnabled: boolean) => {
    try {
      setToggling(domain);
      const action = currentlyEnabled ? 'disable' : 'enable';
      await api.post(`/admin/integrations/${domain}/${action}`);
      toast.success(`Integration ${currentlyEnabled ? 'disabled' : 'enabled'} successfully`);
      fetchIntegrations();
    } catch (error) {
      console.error(`Failed to ${currentlyEnabled ? 'disable' : 'enable'} integration`, error);
      toast.error(`Failed to ${currentlyEnabled ? 'disable' : 'enable'} integration`);
    } finally {
      setToggling(null);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="System Integrations"
        subtitle="Manage global availability of third-party integrations"
        icon={<LinkIcon className="w-8 h-8 text-blue-500" />}
      />

      <div className="bg-white dark:bg-dark-surface shadow rounded-2xl overflow-hidden border border-gray-100 dark:border-dark-border">
        {loading ? (
          <div className="p-8 text-center text-gray-500 dark:text-dark-muted">Loading system integrations...</div>
        ) : integrations.length === 0 ? (
          <div className="p-8 text-center text-gray-500 dark:text-dark-muted">No integrations discovered in the system.</div>
        ) : (
          <ul className="divide-y divide-gray-200 dark:divide-dark-border">
            {integrations.map((integration) => (
              <li key={integration.domain} className="px-6 py-5 flex items-center justify-between hover:bg-gray-50 dark:hover:bg-dark-bg/50 transition-colors">
                <div>
                  <h4 className="text-lg font-bold text-gray-900 dark:text-dark-text">{integration.name || integration.domain}</h4>
                  <p className="text-sm text-gray-500 dark:text-dark-muted mt-1">
                    Integration domain: <code>{integration.domain}</code> {integration.version ? `• v${integration.version}` : ''}
                  </p>
                </div>
                <div className="flex items-center space-x-4">
                  <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                    integration.is_enabled 
                      ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400'
                      : 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-400'
                  }`}>
                    {integration.is_enabled ? 'Enabled' : 'Disabled'}
                  </span>
                  
                  <button
                    onClick={() => handleToggle(integration.domain, integration.is_enabled)}
                    disabled={toggling === integration.domain}
                    className={`inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-xl shadow-sm focus:outline-none transition-all ${
                      integration.is_enabled
                        ? 'bg-red-50 text-red-700 hover:bg-red-100'
                        : 'bg-blue-600 text-white hover:bg-blue-700 shadow-blue-200/50'
                    } disabled:opacity-50`}
                  >
                    {toggling === integration.domain ? (
                      <span className="animate-pulse">Processing...</span>
                    ) : integration.is_enabled ? (
                      <>
                        <PowerOff className="w-4 h-4 mr-2" />
                        Disable
                      </>
                    ) : (
                      <>
                        <Power className="w-4 h-4 mr-2" />
                        Enable
                      </>
                    )}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
};

export default SystemIntegrations;
