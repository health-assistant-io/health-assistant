import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { HardDrive } from 'lucide-react';
import {
  Badge,
  ConnectionTestRow,
  type ConnectionTestStatus,
} from '@neuronection/assistant-ui';

import { PageHeader } from '../../components/ui/PageHeader';
import {
  fetchStorageStatus,
  type StorageStatus,
} from '../../services/storageService';

function checkedLabel(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

/** Admin-only read-only deployment storage surface (plan 55 P4b2).
 *
 * Object storage is instance configuration, not a user setting: there is no
 * credential field, no provider tile and no backend switcher. The card shows
 * what the process resolved plus a live, cached health row; changing the
 * backend is a verified migration, not a save. */
const StorageStatusPage: React.FC = () => {
  const { t } = useTranslation();
  const [data, setData] = useState<StorageStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (refresh: boolean) => {
      if (refresh) setTesting(true);
      setError(null);
      try {
        setData(await fetchStorageStatus(refresh));
      } catch (cause) {
        setError(
          cause instanceof Error && cause.message
            ? cause.message
            : t('storage_status.error', 'Could not read deployment storage status.'),
        );
      } finally {
        setLoading(false);
        setTesting(false);
      }
    },
    [t],
  );

  useEffect(() => {
    void load(false);
  }, [load]);

  const connectionStatus: ConnectionTestStatus =
    loading || testing
      ? 'testing'
      : error
        ? 'fail'
        : data
          ? data.status === 'ok'
            ? 'ok'
            : 'fail'
          : 'idle';
  const target = data?.bucket ?? data?.container;
  const location =
    data === null
      ? null
      : data.backend === 'local'
        ? t('storage_status.local_volume', 'Local volume')
        : [data.region, data.endpoint_host].filter(Boolean).join(' · ') ||
          t('storage_status.provider_default', 'Provider default');
  const failureLabel =
    data?.status === 'degraded'
      ? t('storage_status.degraded', 'Degraded')
      : t('storage_status.unavailable', 'Unavailable');
  const checked = checkedLabel(data?.checked_at ?? null);

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('storage_status.title', 'Storage status')}
        subtitle={t(
          'storage_status.description',
          'Read-only deployment facts for object storage.',
        )}
        icon={<HardDrive className="h-8 w-8 text-blue-500" />}
      />
      <section
        className="rounded-xl border border-gray-200 bg-white p-5 dark:border-dark-border dark:bg-dark-surface"
        data-testid="storage-status-card"
      >
        <header className="flex flex-wrap items-start gap-2">
          <div className="min-w-0 flex-1">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-dark-fg">
              {t('storage_status.title', 'Storage status')}
              {data ? (
                <Badge variant="outline" className="font-mono">
                  {data.backend}
                </Badge>
              ) : null}
            </h2>
            <p className="mt-1 text-xs text-gray-500 dark:text-dark-muted">
              {t(
                'storage_status.read_only_description',
                'Read-only deployment facts. Credentials live in the environment and switching backends is a verified migration, not a settings save.',
              )}
            </p>
          </div>
        </header>

        <div className="mt-4 space-y-3">
          {loading ? (
            <p
              className="text-xs text-gray-500 dark:text-dark-muted"
              role="status"
            >
              {t('storage_status.loading', 'Reading deployment storage status…')}
            </p>
          ) : null}
          {error ? (
            <p className="text-xs text-red-600 dark:text-red-400" role="alert">
              {error}
            </p>
          ) : null}
          {data ? (
            <>
              <dl className="divide-y divide-gray-200 rounded-md border border-gray-200 text-xs dark:divide-dark-border dark:border-dark-border">
                <div className="flex items-center justify-between gap-3 px-3 py-2">
                  <dt className="text-gray-500 dark:text-dark-muted">
                    {t('storage_status.backend', 'Backend')}
                  </dt>
                  <dd className="font-mono">{data.backend}</dd>
                </div>
                <div className="flex items-center justify-between gap-3 px-3 py-2">
                  <dt className="text-gray-500 dark:text-dark-muted">
                    {t('storage_status.target', 'Bucket / container')}
                  </dt>
                  <dd className="truncate font-mono">
                    {target ??
                      (data.backend === 'local'
                        ? t('storage_status.local_volume', 'Local volume')
                        : t('storage_status.none', 'None'))}
                  </dd>
                </div>
                <div className="flex items-center justify-between gap-3 px-3 py-2">
                  <dt className="text-gray-500 dark:text-dark-muted">
                    {t('storage_status.prefix', 'Key prefix')}
                  </dt>
                  <dd className="truncate font-mono">
                    {data.prefix || t('storage_status.none', 'None')}
                  </dd>
                </div>
                <div className="flex items-center justify-between gap-3 px-3 py-2">
                  <dt className="text-gray-500 dark:text-dark-muted">
                    {t('storage_status.location', 'Region / endpoint')}
                  </dt>
                  <dd className="truncate text-right font-mono">{location}</dd>
                </div>
                <div className="flex items-center justify-between gap-3 px-3 py-2">
                  <dt className="text-gray-500 dark:text-dark-muted">
                    {t('storage_status.sse', 'Encryption')}
                  </dt>
                  <dd className="font-mono">
                    {data.sse === 'n/a'
                      ? t('storage_status.not_applicable', 'n/a')
                      : data.sse}
                  </dd>
                </div>
                <div className="flex items-center justify-between gap-3 px-3 py-2">
                  <dt className="text-gray-500 dark:text-dark-muted">
                    {t('storage_status.max_object', 'Max object size')}
                  </dt>
                  <dd className="font-mono">
                    {t('storage_status.megabytes', '{{value}} MB', {
                      value: data.max_object_mb,
                    })}
                  </dd>
                </div>
                <div className="flex items-center justify-between gap-3 px-3 py-2">
                  <dt className="text-gray-500 dark:text-dark-muted">
                    {t('storage_status.presign_ttl', 'Presign TTL')}
                  </dt>
                  <dd className="font-mono">
                    {t('storage_status.seconds', '{{value}} s', {
                      value: data.presign_ttl_seconds,
                    })}
                  </dd>
                </div>
              </dl>
              <ConnectionTestRow
                variant="inline"
                label={t('storage_status.health', 'Connection')}
                status={connectionStatus}
                testLabel={t('storage_status.test', 'Test connection')}
                okLabel={t('storage_status.healthy', 'Healthy')}
                failLabel={failureLabel}
                idleLabel={t('storage_status.not_checked', 'Not checked')}
                errorMessage={data.detail}
                meta={
                  checked
                    ? t('storage_status.checked_at', 'Checked {{time}}', {
                        time: checked,
                      })
                    : null
                }
                onTest={() => void load(true)}
                disabled={loading || testing}
              />
              <p className="text-xs text-gray-500 dark:text-dark-muted">
                {t(
                  'storage_status.read_only',
                  'Read-only. A backend switch runs migrate-objects; it is never a save button.',
                )}
              </p>
            </>
          ) : null}
        </div>
      </section>
    </div>
  );
};

export default StorageStatusPage;
