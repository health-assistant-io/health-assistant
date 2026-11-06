import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import { Link as LinkIcon } from 'lucide-react';
import { EmptyState } from '@neuronection/assistant-ui';
import { PageHeader } from '../../components/ui/PageHeader';
import { LoadingState } from '../../components/ui/LoadingState';
import { IntegrationCard } from '../../components/ui/integrations';
import { adminExtension } from '../../components/integrations/integrationViews';
import {
  integrationService,
  type SystemIntegrationRow,
} from '../../services/integrationService';

const SystemIntegrations = () => {
  const { t } = useTranslation();
  const [rows, setRows] = useState<SystemIntegrationRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyDomain, setBusyDomain] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setRows(await integrationService.listSystemIntegrations());
    } catch (error) {
      console.error('Failed to load system integrations', error);
      toast.error(t('system_integrations.error', 'Failed to load system integrations.'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    load();
  }, [load]);

  const handleToggle = async (domain: string, enabled: boolean) => {
    setBusyDomain(domain);
    try {
      await integrationService.setSystemIntegrationEnabled(domain, enabled);
      toast.success(
        t('system_integrations.toggleSuccess', {
          action: enabled
            ? t('system_integrations.enable', 'Enable').toLowerCase()
            : t('system_integrations.disable', 'Disable').toLowerCase(),
          domain,
          defaultValue: 'Integration {{domain}} updated.',
        }),
      );
      await load();
    } catch (error) {
      console.error(`Failed to update integration ${domain}`, error);
      toast.error(
        t('system_integrations.toggleError', {
          action: enabled
            ? t('system_integrations.enable', 'Enable').toLowerCase()
            : t('system_integrations.disable', 'Disable').toLowerCase(),
          domain,
          defaultValue: 'Failed to update the integration {{domain}}.',
        }),
      );
    } finally {
      setBusyDomain(null);
    }
  };

  const cardLabels = useMemo(
    () => ({
      capabilitiesLabel: t('system_integrations.capabilities', 'Declared capabilities'),
      enableLabel: t('system_integrations.enable', 'Enable'),
      disableLabel: t('system_integrations.disable', 'Disable'),
      enabledAriaLabel: t('system_integrations.toggleAria', 'Global availability'),
    }),
    [t],
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('system_integrations.title', 'System Integrations')}
        subtitle={t(
          'system_integrations.subtitle',
          'Manage global availability of third-party integrations',
        )}
        icon={<LinkIcon className="w-8 h-8 text-blue-500" />}
      />

      {loading ? (
        <LoadingState variant="section" />
      ) : rows.length === 0 ? (
        <EmptyState
          title={t('system_integrations.empty', 'No integrations discovered in the system.')}
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {rows.map((row) => (
            <IntegrationCard
              key={row.domain}
              extension={adminExtension(row)}
              instanceCount={null}
              enabled={row.is_enabled}
              busy={busyDomain === row.domain}
              labels={cardLabels}
              onEnable={(extension, enabled) => handleToggle(extension.id, enabled)}
            >
              <div className="text-xs text-gray-500 dark:text-dark-muted space-y-1">
                <p>
                  {t('system_integrations.domain', 'Integration domain')}:{' '}
                  <code className="font-mono">{row.domain}</code>
                </p>
                {!row.on_filesystem && (
                  <p className="text-amber-600 dark:text-amber-400">
                    {t('system_integrations.stale', 'Unknown (not on filesystem)')}
                  </p>
                )}
              </div>
            </IntegrationCard>
          ))}
        </div>
      )}
    </div>
  );
};

export default SystemIntegrations;
