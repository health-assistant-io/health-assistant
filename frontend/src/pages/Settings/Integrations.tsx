import React, { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { integrationService, IntegrationManifest, ActiveIntegration } from '../../services/integrationService';
import { toast } from 'react-toastify';
import { Search, LayoutGrid, Plug, Settings } from 'lucide-react';
import ConfigFlowModal from '../../components/integrations/ConfigFlowModal';
import IntegrationDocsDialog from '../../components/integrations/IntegrationDocsDialog';
import BrowseIntegrationsModal from '../../components/integrations/BrowseIntegrationsModal';
import { instanceGroups } from '../../components/integrations/integrationViews';
import { IntegrationInstancesList } from '../../components/ui/integrations';
import { NoPatientState } from '../../components/ui/NoPatientState';
import { PageHeader } from '../../components/ui/PageHeader';
import { usePatientStore } from '../../store/slices/patientSlice';

const Integrations: React.FC = () => {
  const { t } = useTranslation();
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  const groups = useMemo(() => instanceGroups(active, available), [active, available]);

  // Get unique categories from available integrations (for the browse modal badge).
  const categoryCount = Array.from(new Set(available.flatMap(i => i.categories || ['Uncategorized']))).length;
  const connectedDomains = useMemo(() => new Set(active.map((i) => i.domain)), [active]);

  if (!currentPatient) return <NoPatientState icon={Plug} contextKey="integrations" />;
  if (loading) return <div className="p-8 text-center text-gray-500">Loading Integrations...</div>;

  return (
    <div className="max-w-5xl mx-auto py-8 px-4">
      <PageHeader
        title={t('common.integrations', 'Integrations')}
        subtitle={t('integrations.subtitle', 'Manage your connected health data sources.')}
        icon={<Plug className="w-8 h-8" />}
      />

      {/* Active Integrations — the kit's grouped instances list (G1: one
          extension, N configured occurrences). Details/remove stay app
          actions in the instance-actions slot. */}
      <div className="bg-white dark:bg-dark-surface shadow-sm rounded-lg mb-8 overflow-hidden border border-gray-200 dark:border-dark-border">
        <div className="px-4 py-5 sm:px-6 border-b border-gray-200 dark:border-dark-border">
          <h3 className="text-lg leading-6 font-medium text-gray-900 dark:text-white">Your Integrations</h3>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Manage your connected health data sources.</p>
        </div>
        <div className="px-4 py-4 sm:px-6">
          <IntegrationInstancesList
            groups={groups}
            labels={{
              emptyLabel: t('integrations.list.empty', 'No active integrations found.'),
              enableAriaLabel: t('integrations.list.enableAria', 'Enable'),
              tombstoneLabel: t('integrations.list.remove', 'Remove'),
              conditionLabels: {
                'needs-config': t('integrations.list.needsConfig', 'needs config'),
                'needs-secret': t('integrations.list.needsSecret', 'needs secret'),
              },
              lastSuccessLabel: t('integrations.list.lastSynced', 'Last synced'),
              failuresLabel: t('integrations.list.failures', 'consecutive failures'),
              statusLabels: {
                ok: t('integrations.list.statusOk', 'Connected'),
                degraded: t('integrations.list.statusDegraded', 'Degraded'),
                failing: t('integrations.list.statusFailing', 'Error — requires attention'),
                stale: t('integrations.list.statusStale', 'Pending setup'),
              },
            }}
            onTombstone={(instance) => void handleRemove(instance.id, String(instance.metadata?.domain ?? instance.extensionId ?? ''))}
            renderInstanceActions={(instance) => (
              <Link
                to={`/settings/integrations/${instance.id}`}
                className="inline-flex items-center px-3 py-1.5 border border-gray-300 dark:border-gray-600 text-xs font-medium rounded-sm shadow-xs text-gray-700 dark:text-gray-200 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700 focus:outline-hidden"
              >
                <Settings className="h-4 w-4 mr-1" aria-hidden />
                {t('integrations.list.details', 'Details')}
              </Link>
            )}
          />
        </div>
      </div>

      {/* Available Integrations — launcher card */}
      <div className="bg-white dark:bg-dark-surface shadow-sm rounded-lg border border-gray-200 dark:border-dark-border">
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
            className="inline-flex items-center justify-center gap-2 px-5 py-2.5 bg-blue-600 text-white rounded-xl text-sm font-semibold hover:bg-blue-700 shadow-xs shadow-blue-200/50 transition-colors shrink-0"
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
        <IntegrationDocsDialog
          domain={docsDomain}
          onClose={() => setDocsDomain(null)}
        />
      )}
    </div>
  );
};

export default Integrations;
